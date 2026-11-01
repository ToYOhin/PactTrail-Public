"""One explicit DeepSeek Search request; never Fetch or model completion.

Protocol: https://api-docs.deepseek.com/api/search/ (checked 2026-10-19).
Credentials and billing/checkpoint hooks come only from authorized composition.
DNS, sockets and the clock are injectable for offline synthetic transport tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
import json
import math
import re
import socket
import ssl
import threading
import time
import uuid

from pacttrail.domain.web_search import WebSearchError, validate_search
from pacttrail.domain.web_source import public_address, validate_url
from pacttrail.domain.runs import PactTrailError


SEARCH_ENDPOINT = 'https://api.deepseek.com/v1/tools/search'
MAX_QUERY_CHARS = 128
MAX_RESPONSE_BYTES = 65536
MAX_SECONDS = 8
_TARGET = validate_url(SEARCH_ENDPOINT)


@dataclass
class _Call:
    started: float
    deadline: float
    cancel: threading.Event
    sock: object = None
    timer: object = None
    timed_out: bool = False


def _resolve(host, port):
    values = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not 1 <= len(values) <= 16:
        raise WebSearchError()
    result = []
    for family, kind, protocol, _, address in values:
        if (family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM
                or protocol != socket.IPPROTO_TCP or address[1] != port
                or family == socket.AF_INET6 and (address[2] or address[3])):
            raise WebSearchError()
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
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host,
                                              do_handshake_on_connect=False)
        self._register(self.sock)
        self.sock.do_handshake()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError()


def _plain(value, maximum, *, empty=False):
    if (type(value) is not str
            or any(ord(char) < 32 or 127 <= ord(char) <= 159
                   or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise ValueError()
    value = value.strip()[:maximum]
    if not empty and not value:
        raise ValueError()
    return value


class DeepSeekSearcher:
    """One synchronous search, with physical busy state retained through cleanup.

    ``before_dispatch(reservation)`` durably records pending work before POST.
    ``after_dispatch(reservation)`` records the guard's settled state. An
    indeterminate external outcome retains the reservation and stops the guard.
    Connection factories use WebFetcher's target/address injection signature.
    """

    def __init__(self, token, *, admission, before_dispatch, after_dispatch,
                 resolver=None, connection_factory=None, clock=time.monotonic, utc_now=None):
        if ((token is not None and (type(token) is not str or not 1 <= len(token) <= 512
                                    or any(not 33 <= ord(char) <= 126 for char in token)))
                or not callable(getattr(admission, 'reserve_search', None))
                or not callable(getattr(admission, 'settle_search', None))
                or not callable(before_dispatch) or not callable(after_dispatch)
                or resolver is not None and not callable(resolver)
                or connection_factory is not None and not callable(connection_factory)
                or not callable(clock) or utc_now is not None and not callable(utc_now)):
            raise WebSearchError()
        self._token, self._admission = token, admission
        self._before, self._after = before_dispatch, after_dispatch
        self._resolver = _resolve if resolver is None else resolver
        self._factory, self._clock = connection_factory, clock
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
        self._lock = threading.Lock()
        self._active = None
        self._closed = self._used = False

    def status(self):
        with self._lock:
            return {'busy': self._active is not None, 'closed': self._closed}

    def _reserve(self):
        try:
            return self._admission.reserve_search(uuid.uuid4().hex)
        except PactTrailError as error:
            if error.args == ('model_evidence_error: usage_unavailable',):
                raise WebSearchError('usage_unavailable') from None
            if error.args in (('model_evidence_error: budget_limit',),
                              ('model_evidence_error: request_limit',)):
                raise WebSearchError('budget_limit') from None
            raise WebSearchError() from None

    @staticmethod
    def _checkpoint(callback, reservation):
        try:
            callback(reservation)
        except Exception:
            # A failed durable boundary cannot become an "empty" result that
            # SearchResearch is permitted to replace with a second paid query.
            raise WebSearchError('usage_unavailable') from None

    def _settle(self, reservation, outcome):
        try:
            self._admission.settle_search(reservation, outcome)
        except Exception:
            raise WebSearchError('usage_unavailable') from None
        self._checkpoint(self._after, reservation)

    def _now(self):
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise WebSearchError()
        return value

    def _remaining(self, call):
        if call.timed_out:
            raise WebSearchError('timeout')
        if call.cancel.is_set() or self._closed:
            raise WebSearchError('cancelled')
        now = self._now()
        if now < call.started:
            raise WebSearchError()
        if now >= call.deadline:
            raise WebSearchError('timeout')
        return call.deadline - now

    @staticmethod
    def _abort(sock):
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def _register(self, call, sock):
        with self._lock:
            call.sock = sock
            cancelled = self._closed or call.cancel.is_set() or call.timed_out
        if cancelled:
            self._abort(sock)
        sock.settimeout(self._remaining(call))

    def _expired(self, call):
        with self._lock:
            if self._active is not call or self._closed or call.cancel.is_set():
                return
            call.timed_out = True
            sock = call.sock
        self._abort(sock)

    def close(self):
        with self._lock:
            self._closed, self._token = True, None
            call = self._active
            if call is None:
                return
            call.cancel.set()
            sock, timer = call.sock, call.timer
        if timer is not None:
            timer.cancel()
        self._abort(sock)

    def _address(self, call):
        # A late resolver can finish DNS only; it never owns a connection/token.
        done, answer = threading.Event(), []
        def resolve():
            try:
                answer.append(self._resolver(_TARGET['host'], _TARGET['port']))
            except Exception:
                answer.append(None)
            finally:
                done.set()
        threading.Thread(target=resolve, daemon=True, name='pacttrail-search-dns').start()
        while not done.wait(min(.05, self._remaining(call))):
            self._remaining(call)
        self._remaining(call)
        values = answer[0]
        if type(values) not in (list, tuple) or not 1 <= len(values) <= 16:
            raise WebSearchError()
        addresses = [public_address(value) for value in values]
        return addresses[0]

    def _body(self, response, call):
        entries = response.getheaders()
        if type(entries) is not list or len(entries) > 32:
            raise WebSearchError()
        headers, total = {}, 0
        for key, value in entries:
            if (type(key) is not str or type(value) is not str
                    or not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", key)
                    or any(ord(char) < 32 and char != '\t' or ord(char) == 127 for char in value)):
                raise WebSearchError()
            key = key.lower()
            total += len(key) + len(value)
            if total > 8192 or key in headers and key in {
                    'content-type', 'content-length', 'content-encoding', 'transfer-encoding'}:
                raise WebSearchError()
            headers[key] = value.strip()
        content_type = headers.get('content-type', '').split(';', 1)[0].strip().lower()
        length, transfer = headers.get('content-length'), headers.get('transfer-encoding')
        if (content_type != 'application/json'
                or headers.get('content-encoding', 'identity').lower() not in ('', 'identity')
                or transfer is not None and (transfer.lower() != 'chunked' or length is not None)):
            raise WebSearchError()
        expected = None
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 10:
                raise WebSearchError()
            expected = int(length)
            if expected > MAX_RESPONSE_BYTES:
                raise WebSearchError('response_limit')
        chunks, total = [], 0
        while not response.isclosed():
            call.sock.settimeout(self._remaining(call))
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - total))
            self._remaining(call)
            if type(chunk) is not bytes:
                raise WebSearchError()
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise WebSearchError('response_limit')
            chunks.append(chunk)
        if expected is not None and total != expected:
            raise WebSearchError()
        return b''.join(chunks)

    def _results(self, query, entries):
        result, seen = [], set()
        for entry in entries:
            try:
                if type(entry) is not dict:
                    raise ValueError()
                title = _plain(entry.get('title'), 160)
                snippet = _plain(entry.get('snippet'), 600, empty=True)
                url = validate_url(entry.get('url'))['url']
            except Exception:
                continue
            if url in seen:
                continue
            seen.add(url)
            result.append(dict(title=title, url=url, snippet=snippet))
            if len(result) == 3:
                break
        if not result:
            raise WebSearchError('empty')
        return validate_search(dict(version=1, query=query, searched_at=self._utc_now(),
                                    provider='deepseek', transport='http', results=result))

    def search(self, query, *, cancel, deadline):
        if (type(query) is not str or not 1 <= len(query) <= MAX_QUERY_CHARS
                or not query.strip() or ' '.join(query.split()) != query
                or any(ord(char) < 32 or 127 <= ord(char) <= 159
                       or 0xD800 <= ord(char) <= 0xDFFF for char in query)
                or not isinstance(cancel, threading.Event)
                or type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise WebSearchError()
        with self._lock:
            if self._closed or self._used or self._active is not None or self._token is None:
                raise WebSearchError()
            started = self._now()
            call = _Call(started, min(deadline, started + MAX_SECONDS), cancel)
            self._active, self._used, token = call, True, self._token
        connection = response = reservation = None
        dispatched = finalized = False
        outcome = 'free'
        try:
            self._remaining(call)
            call.timer = threading.Timer(self._remaining(call), self._expired, args=(call,))
            call.timer.daemon = True
            call.timer.start()
            address = self._address(call)
            context = ssl.create_default_context()
            context.set_alpn_protocols(['http/1.1'])
            remaining = self._remaining(call)
            if self._factory is None:
                connection = _PinnedConnection(dict(_TARGET), address, timeout=remaining,
                    context=context, register=lambda sock: self._register(call, sock))
            else:
                connection = self._factory(dict(_TARGET), address, timeout=remaining, context=context)
            connection.set_debuglevel(0)
            if connection.sock is not None:
                self._register(call, connection.sock)
            self._remaining(call)
            connection.connect()
            self._register(call, connection.sock)
            reservation = self._reserve()
            self._checkpoint(self._before, reservation)
            self._remaining(call)
            body = json.dumps({'query': query, 'count': 3, 'need_content': False},
                              ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            # Mark the attempt before request(): an exception may follow a partial send.
            dispatched, outcome = True, 'unknown'
            connection.request('POST', _TARGET['target'], body=body, headers={
                'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                'Accept': 'application/json', 'Accept-Encoding': 'identity', 'Connection': 'close',
            }, encode_chunked=False)
            call.sock.settimeout(self._remaining(call))
            response = connection.getresponse()
            self._remaining(call)
            status = response.status
            if type(status) is not int or not 100 <= status <= 599:
                raise WebSearchError()
            if status != 200:
                outcome = 'free'
                raise WebSearchError()
            raw = self._body(response, call)
            parsed = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                                parse_constant=_invalid_constant)
            if type(parsed) is not dict or type(parsed.get('results')) is not list:
                raise WebSearchError()
            entries = parsed['results']
            outcome = 'billable' if entries else 'free'
            finalized = True
            self._settle(reservation, outcome)
            if not entries:
                raise WebSearchError('empty')
            # Charge the service response before any local URL/relevance filtering.
            result = self._results(query, entries)
            self._remaining(call)
            return result
        except WebSearchError:
            raise
        except Exception as error:
            try:
                self._remaining(call)
            except WebSearchError as terminal:
                raise terminal from None
            raise WebSearchError('timeout' if isinstance(error, TimeoutError) else 'unavailable') from None
        finally:
            try:
                if reservation is not None and not finalized:
                    finalized = True
                    self._settle(reservation, outcome if dispatched else 'free')
                if dispatched and outcome == 'unknown':
                    # Preserve the retained reservation as the public failure,
                    # including a timeout/cancel after a possible partial POST.
                    raise WebSearchError('usage_unavailable')
            except WebSearchError:
                raise
            except Exception:
                raise WebSearchError('usage_unavailable') from None
            finally:
                if call.timer is not None:
                    call.timer.cancel()
                for resource in (response, connection):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception:
                            pass
                with self._lock:
                    if self._active is call:
                        self._active = None
