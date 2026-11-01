"""Bounded synchronous public-page acquisition; callers own the sole worker."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import http.client
import ipaddress
import math
import re
import socket
import ssl
import threading
import time
from urllib.parse import urljoin

from pacttrail.domain.web_source import WebSourceError, public_address, validate_url, validate_urls


_REDIRECTS = (301, 302, 303, 307, 308)


def _resolve(host, port):
    values = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not 1 <= len(values) <= 16:
        raise WebSourceError('forbidden_address')
    result = []
    for family, kind, protocol, _, address in values:
        if (family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM
                or protocol != socket.IPPROTO_TCP or address[1] != port
                or family == socket.AF_INET6 and (address[2] != 0 or address[3] != 0)):
            raise WebSourceError('forbidden_address')
        result.append(address[0])
    return result


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, target, address, *, timeout, context, register):
        super().__init__(target['host'], target['port'], timeout=timeout)
        self._address, self._context, self._register = address, context, register

    def connect(self):
        family = socket.AF_INET6 if ':' in self._address else socket.AF_INET
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self._register(self.sock)
        destination = ((self._address, self.port, 0, 0) if family == socket.AF_INET6
                       else (self._address, self.port))
        self.sock.connect(destination)
        if self._context is not None:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host,
                                                  do_handshake_on_connect=False)
            self._register(self.sock)
            self.sock.do_handshake()


class _Text(HTMLParser):
    _IGNORE = frozenset(('head', 'script', 'style', 'template', 'noscript', 'iframe',
                         'object', 'embed', 'svg', 'math', 'canvas', 'audio', 'video'))
    _BLOCK = frozenset(('p', 'div', 'section', 'article', 'header', 'footer', 'main', 'aside',
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ul', 'ol', 'pre',
                        'blockquote', 'table', 'tr', 'br', 'hr'))

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.ignored = [], []
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 8192 or len(self.ignored) > 64:
            raise WebSourceError('response_limit')
        if tag == 'embed':
            return  # HTML's void embed element has no body to retain or skip.
        if tag in self._IGNORE:
            self.ignored.append(tag)
        elif not self.ignored and tag in self._BLOCK:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in self.ignored:
            index = len(self.ignored) - 1 - self.ignored[::-1].index(tag)
            del self.ignored[index:]
        elif not self.ignored and tag in self._BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(re.sub(r'\s+', ' ', data))

    def text(self):
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(line.strip() for line in ''.join(self.parts).split('\n'))).strip()


@dataclass
class _Call:
    started: float
    deadline: float
    cancel: threading.Event
    sock: object = None
    timer: object = None
    timed_out: bool = False


class WebFetcher:
    """No retries or hidden DNS workers; close aborts the current socket promptly."""

    def __init__(self, *, resolver=None, connection_factory=None, clock=time.monotonic, utc_now=None):
        if (resolver is not None and not callable(resolver)
                or connection_factory is not None and not callable(connection_factory)
                or not callable(clock) or utc_now is not None and not callable(utc_now)):
            raise WebSourceError('unavailable')
        self._resolver = _resolve if resolver is None else resolver
        self._factory = connection_factory
        self._clock = clock
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
        self._lock = threading.Lock()
        self._active = None
        self._closed = False

    def status(self):
        with self._lock:
            return {'busy': self._active is not None, 'closed': self._closed}

    def _now(self):
        try:
            value = self._clock()
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError()
            return value
        except Exception:
            raise WebSourceError('unavailable') from None

    def _remaining(self, call):
        if call.timed_out:
            raise WebSourceError('timeout')
        if call.cancel.is_set() or self._closed:
            raise WebSourceError('cancelled')
        now = self._now()
        if now < call.started:
            raise WebSourceError('unavailable')
        if now >= call.deadline:
            raise WebSourceError('timeout')
        return call.deadline - now

    def _register(self, call, sock):
        with self._lock:
            call.sock = sock
            cancelled = self._closed or call.cancel.is_set() or call.timed_out
        if cancelled:
            self._abort_socket(sock)
        sock.settimeout(self._remaining(call))

    @staticmethod
    def _abort_socket(sock):
        # shutdown wakes a socket's makefile reader; close also makes an as-yet
        # unconnected socket unusable if cancellation wins the connect race.
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def _deadline_expired(self, call):
        # One bounded control timer, never a network/DNS worker. Identity guards
        # prevent a late timer from closing any later acquisition's socket.
        with self._lock:
            if self._active is not call or self._closed or call.cancel.is_set():
                return
            call.timed_out = True
            sock = call.sock
        self._abort_socket(sock)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            call = self._active
            if call is not None:
                call.cancel.set()
                sock = call.sock
                timer = call.timer
            else:
                sock = timer = None
        if timer is not None:
            timer.cancel()
        self._abort_socket(sock)

    def _addresses(self, target, call):
        self._remaining(call)
        try:
            ipaddress.ip_address(target['host'])
            values = [target['host']]
        except ValueError:
            values = self._resolver(target['host'], target['port'])
        self._remaining(call)
        if type(values) not in (list, tuple) or not 1 <= len(values) <= 16:
            raise WebSourceError('forbidden_address')
        return [public_address(value) for value in values]

    @staticmethod
    def _headers(response):
        entries = response.getheaders()
        if type(entries) is not list or len(entries) > 32:
            raise WebSourceError('invalid_response')
        result, total = {}, 0
        critical = {'content-type', 'content-length', 'content-encoding', 'transfer-encoding', 'location'}
        for key, value in entries:
            if (type(key) is not str or type(value) is not str
                    or not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", key)
                    or any(ord(c) < 32 and c != '\t' or ord(c) == 127 for c in value)):
                raise WebSourceError('invalid_response')
            total += len(key) + len(value)
            key = key.lower()
            if total > 8192 or key in result and key in critical:
                raise WebSourceError('invalid_response')
            result[key] = value.strip()
        return result

    def _body(self, response, headers, call):
        content_type = headers.get('content-type', '')
        match = re.fullmatch(r'(text/plain|text/html)(?:\s*;\s*charset\s*=\s*(?:"utf-8"|utf-8))?',
                             content_type, re.I)
        if match is None or headers.get('content-encoding', 'identity').lower() != 'identity':
            raise WebSourceError('invalid_response')
        media_type = match[1].lower()
        transfer, length = headers.get('transfer-encoding'), headers.get('content-length')
        if transfer is not None and (transfer.lower() != 'chunked' or length is not None):
            raise WebSourceError('invalid_response')
        expected = None
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 10:
                raise WebSourceError('invalid_response')
            expected = int(length)
            if expected > 65536:
                raise WebSourceError('response_limit')
        chunks, total = [], 0
        while not response.isclosed():
            call.sock.settimeout(self._remaining(call))
            chunk = response.read1(min(8192, 65537 - total))
            self._remaining(call)
            if type(chunk) is not bytes:
                raise WebSourceError('invalid_response')
            if not chunk:
                break
            total += len(chunk)
            if total > 65536:
                raise WebSourceError('response_limit')
            chunks.append(chunk)
        if expected is not None and total != expected:
            raise WebSourceError('invalid_response')
        text = b''.join(chunks).decode('utf-8')
        if any((ord(c) < 32 and c not in '\r\n\t') or ord(c) == 127 for c in text):
            raise WebSourceError('invalid_response')
        if media_type == 'text/html':
            parser = _Text()
            parser.feed(text)
            parser.close()
            text = parser.text()
        if any((ord(c) < 32 and c not in '\r\n\t') or ord(c) == 127 for c in text):
            raise WebSourceError('invalid_response')
        if not any(not c.isspace() and c != '\ufeff' for c in text):
            raise WebSourceError('invalid_response')
        if (len(text.encode('utf-8')) > 8192
                or len(text.replace('\r\n', '\n').replace('\r', '\n').split('\n')) > 256):
            raise WebSourceError('response_limit')
        return media_type, text

    def _page(self, first, call):
        target, seen = first, {first['url']}
        for hops in range(4):
            address = self._addresses(target, call)[0]
            context = None
            if target['scheme'] == 'https':
                context = ssl.create_default_context()
                context.set_alpn_protocols(['http/1.1'])
            connection = response = None
            try:
                remaining = self._remaining(call)
                if self._factory is None:
                    connection = _PinnedConnection(target, address, timeout=remaining, context=context,
                                                    register=lambda sock: self._register(call, sock))
                else:
                    connection = self._factory(dict(target), address, timeout=remaining, context=context)
                connection.set_debuglevel(0)
                if connection.sock is not None:
                    self._register(call, connection.sock)
                self._remaining(call)
                connection.connect()
                self._register(call, connection.sock)
                host = '[' + target['host'] + ']' if ':' in target['host'] else target['host']
                connection.request('GET', target['target'], body=None, headers={
                    'Host': host, 'Accept': 'text/plain, text/html', 'Accept-Encoding': 'identity',
                    'User-Agent': 'PactTrail/0.1', 'Connection': 'close',
                }, encode_chunked=False)
                call.sock.settimeout(self._remaining(call))
                response = connection.getresponse()
                self._remaining(call)
                headers = self._headers(response)
                if type(response.status) is not int or not 100 <= response.status <= 599:
                    raise WebSourceError('invalid_response')
                if response.status in _REDIRECTS:
                    location = headers.get('location')
                    if not location:
                        raise WebSourceError('invalid_response')
                    if any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in location):
                        raise WebSourceError('invalid_url')
                    chosen = validate_url(urljoin(target['url'], location))
                    if target['scheme'] == 'https' and chosen['scheme'] != 'https':
                        raise WebSourceError('invalid_url')
                    if hops == 3 or chosen['url'] in seen:
                        raise WebSourceError('redirect_limit')
                    seen.add(chosen['url'])
                    target = chosen
                    continue
                if response.status != 200:
                    raise WebSourceError('invalid_response')
                media_type, text = self._body(response, headers, call)
                self._remaining(call)
                fetched_at = self._utc_now()
                if (type(fetched_at) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', fetched_at)
                        or datetime.strptime(fetched_at, '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%dT%H:%M:%SZ') != fetched_at):
                    raise WebSourceError('unavailable')
                return {'requested_url': first['url'], 'final_url': target['url'], 'fetched_at': fetched_at,
                        'media_type': media_type, 'text': text}
            finally:
                cleanup_failed = False
                for resource in (response, connection):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception:
                            cleanup_failed = True
                if call.sock is not None:
                    try:
                        call.sock.close()
                    except Exception:
                        cleanup_failed = True
                with self._lock:
                    call.sock = None
                if cleanup_failed:
                    raise WebSourceError('unavailable')
        raise WebSourceError('redirect_limit')

    def fetch(self, urls, *, timeout_seconds=15, cancel=None):
        targets = validate_urls(urls)
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 15 or cancel is not None and not isinstance(cancel, threading.Event)):
            raise WebSourceError('invalid_url')
        with self._lock:
            if self._active is not None:
                raise WebSourceError('busy')
            if self._closed:
                raise WebSourceError('cancelled')
            started = self._now()
            call = _Call(started, started + timeout_seconds, cancel or threading.Event())
            self._active = call
        try:
            self._remaining(call)
            call.timer = threading.Timer(self._remaining(call), self._deadline_expired, args=(call,))
            call.timer.daemon = True
            call.timer.start()
            self._remaining(call)
            result, total = [], 0
            for target in targets:
                item = self._page(target, call)
                total += len(item['text'].encode('utf-8'))
                if total > 8192:
                    raise WebSourceError('response_limit')
                result.append(item)
            self._remaining(call)
            return result
        except Exception as error:
            self._remaining(call)
            if isinstance(error, WebSourceError):
                raise error from None
            if isinstance(error, TimeoutError):
                raise WebSourceError('timeout') from None
            if isinstance(error, (UnicodeError, http.client.HTTPException)):
                raise WebSourceError('invalid_response') from None
            raise WebSourceError('unavailable') from None
        finally:
            if call.timer is not None:
                call.timer.cancel()
            with self._lock:
                if self._active is call:
                    self._active = None
