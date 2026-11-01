"""Search-only public-page excerpts; the old whole-page fetch policy is unchanged."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import math
import re
import threading
import time
import zlib

from pacttrail.domain.web_search import WebSearchError
from pacttrail.domain.web_source import WebSourceError, validate_url
from pacttrail.platform.web_fetch import WebFetcher, _Call, _resolve


_WIRE_BYTES = 65536
_HTML_BYTES = 262144
_TEXT_BYTES = 4096
_IGNORED = frozenset(('head', 'script', 'style', 'template', 'noscript', 'iframe',
                      'object', 'embed', 'svg', 'math', 'canvas', 'audio', 'video',
                      'nav', 'footer', 'form', 'aside'))
_VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
                   'meta', 'param', 'source', 'track', 'wbr'))
_BLOCK = frozenset(('p', 'div', 'section', 'article', 'header', 'main', 'h1', 'h2',
                    'h3', 'h4', 'h5', 'h6', 'li', 'ul', 'ol', 'pre', 'blockquote',
                    'table', 'tr', 'br', 'hr'))


class _Content(HTMLParser):
    """Static visible-text approximation; never load CSS, scripts or subresources."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts, self.main_parts = [], [], []
        self.nodes = 0

    def _append(self, text):
        if not any(hidden for _, hidden, _ in self.stack):
            self.parts.append(text)
            if any(main for _, _, main in self.stack):
                self.main_parts.append(text)

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 8192 or len(self.stack) >= 64:
            raise WebSourceError('response_limit')
        values = dict(attrs)
        hidden = (tag in _IGNORED or 'hidden' in values
                  or (values.get('aria-hidden') or '').lower() == 'true'
                  or re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)\b',
                               values.get('style') or '', re.I) is not None)
        if tag not in _VOID:
            self.stack.append((tag, hidden, tag == 'main' or values.get('role') == 'main'))
        if not hidden and tag in _BLOCK:
            self._append('\n')

    def handle_endtag(self, tag):
        if tag in _BLOCK:
            self._append('\n')
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self._append(re.sub(r'\s+', ' ', data))

    def text(self):
        main = ''.join(self.main_parts)
        return main if main.strip() else ''.join(self.parts)


def _fragment(line, terms):
    """Long paragraphs are windowed around a match, never at the page prefix."""
    if len(line.encode('utf-8')) <= 1024:
        return line
    positions = [line.lower().find(term) for term in terms if term in line.lower()]
    start = max(0, min(positions, default=0) - 100)
    prefix = '…' if start else ''
    body = line[start:].encode('utf-8')[:1018].decode('utf-8', errors='ignore')
    return prefix + body + ('…' if start + len(body) < len(line) else '')


def _excerpt(text, terms):
    lines = [' '.join(line.split()) for line in text.splitlines() if line.strip()]
    ranked = []
    for index, line in enumerate(lines):
        lower = line.lower()
        relevance = sum(term in lower for term in terms)
        if not relevance:
            continue
        nearby = ' '.join(lines[max(0, index - 1):index + 2]).lower()
        score = (4 * relevance + 10 * bool(re.search(r'\bstable\b|稳定', nearby))
                 + 8 * bool(re.search(r'\blatest\b|\bnewest\b|最新', nearby))
                 + 2 * bool(re.search(r'\bdownload\b|下载', lower)))
        ranked.append((-score, index))
    if not ranked:
        raise WebSearchError('empty')
    selected, used = {}, 0
    for _, index in sorted(ranked):
        # First preserve the evidence-bearing line, then its adjacent context.
        for chosen in (index, index - 1, index + 1):
            if chosen in selected or chosen < 0 or chosen >= len(lines):
                continue
            fragment = _fragment(lines[chosen], terms)
            cost = len(fragment.encode('utf-8')) + (1 if selected else 0)
            if used + cost > _TEXT_BYTES:
                continue
            selected[chosen] = fragment
            used += cost
        if len(selected) >= 24:
            break
    if not selected:
        raise WebSearchError('empty')
    return '\n'.join(selected[index] for index in sorted(selected))


class SearchPageReader(WebFetcher):
    """One optional read inside a search's existing absolute deadline.

    TLS, numeric pinning, header validation and redirect limits come from
    WebFetcher. DNS may finish late but cannot itself create an HTTP request.
    The independent gzip/selection policy never changes whole-page WebFetcher.
    """

    def __init__(self, *, resolver=None, connection_factory=None, clock=time.monotonic, utc_now=None):
        self._page_resolver = _resolve if resolver is None else resolver
        self._used, self._terms, self._official_domains = False, (), ()
        try:
            if not callable(self._page_resolver):
                raise WebSourceError()
            super().__init__(resolver=self._bounded_resolve, connection_factory=connection_factory,
                             clock=clock, utc_now=utc_now)
        except WebSourceError:
            raise WebSearchError() from None

    def _bounded_resolve(self, host, port):
        call, done, answer = self._active, threading.Event(), []
        def resolve():
            try:
                answer.append((True, self._page_resolver(host, port)))
            except Exception:
                answer.append((False, None))
            finally:
                done.set()
        worker = threading.Thread(target=resolve, daemon=True, name='pacttrail-page-dns')
        worker.start()
        while not done.wait(min(.05, self._remaining(call))):
            self._remaining(call)
        self._remaining(call)
        if not answer or not answer[0][0]:
            raise WebSourceError()
        return answer[0][1]

    def _addresses(self, target, call):
        # A redirect outside an explicit official scope is refused before DNS.
        if self._official_domains and not any(
                target['host'] == host or target['host'].endswith('.' + host)
                for host in self._official_domains):
            raise WebSourceError('invalid_url')
        return super()._addresses(target, call)

    def _body(self, response, headers, call):
        match = re.fullmatch(r'(text/plain|text/html)(?:\s*;\s*charset\s*=\s*(?:"utf-8"|utf-8))?',
                             headers.get('content-type', ''), re.I)
        encoding = headers.get('content-encoding', 'identity').lower()
        if match is None or encoding not in ('identity', 'gzip'):
            raise WebSourceError('invalid_response')
        transfer, length = headers.get('transfer-encoding'), headers.get('content-length')
        if transfer is not None and (transfer.lower() != 'chunked' or length is not None):
            raise WebSourceError('invalid_response')
        expected = None
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 10:
                raise WebSourceError('invalid_response')
            expected = int(length)
            if expected > _WIRE_BYTES:
                raise WebSourceError('response_limit')
        chunks, total = [], 0
        while not response.isclosed():
            call.sock.settimeout(self._remaining(call))
            chunk = response.read1(min(8192, _WIRE_BYTES + 1 - total))
            self._remaining(call)
            if type(chunk) is not bytes:
                raise WebSourceError('invalid_response')
            if not chunk:
                break
            total += len(chunk)
            if total > _WIRE_BYTES:
                raise WebSourceError('response_limit')
            chunks.append(chunk)
        if expected is not None and total != expected:
            raise WebSourceError('invalid_response')
        raw = b''.join(chunks)
        if encoding == 'gzip':
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            raw = decoder.decompress(raw, _HTML_BYTES + 1)
            if len(raw) > _HTML_BYTES:
                raise WebSourceError('response_limit')
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise WebSourceError('invalid_response')
        self._remaining(call)
        text = raw.decode('utf-8')
        if any(ord(char) < 32 and char not in '\r\n\t' or 127 <= ord(char) <= 159
               for char in text):
            raise WebSourceError('invalid_response')
        if match[1].lower() == 'text/html':
            parser = _Content()
            parser.feed(text)
            parser.close()
            text = parser.text()
        if any(ord(char) < 32 and char not in '\r\n\t' or 127 <= ord(char) <= 159
               for char in text):
            raise WebSourceError('invalid_response')
        self._remaining(call)
        excerpt = _excerpt(text, self._terms)
        self._remaining(call)
        return match[1].lower(), excerpt

    def read(self, url, *, terms, cancel, deadline, official_domains=()):
        try:
            target = validate_url(url)
            if (type(terms) not in (list, tuple) or len(terms) > 12
                    or any(type(term) is not str or not 1 <= len(term) <= 64
                           or any(ord(char) < 32 or 127 <= ord(char) <= 159
                                  or 0xD800 <= ord(char) <= 0xDFFF for char in term)
                           for term in terms)
                    or type(official_domains) not in (list, tuple) or len(official_domains) > 3
                    or any(type(host) is not str
                           or validate_url('https://' + host + '/')['host'] != host
                           for host in official_domains)
                    or not isinstance(cancel, threading.Event)
                    or type(deadline) not in (int, float) or not math.isfinite(deadline)):
                raise WebSourceError()
        except (WebSourceError, ValueError, TypeError):
            raise WebSearchError() from None
        with self._lock:
            if self._closed or cancel.is_set():
                raise WebSearchError('cancelled')
            if self._used or self._active is not None:
                raise WebSearchError()
            self._used = True
            try:
                started = self._now()
            except WebSourceError:
                raise WebSearchError() from None
            call = _Call(started, deadline, cancel)
            self._active = call
            self._terms = tuple(term.lower() for term in terms)
            self._official_domains = tuple(official_domains)
        try:
            self._remaining(call)
            call.timer = threading.Timer(self._remaining(call), self._deadline_expired, args=(call,))
            call.timer.daemon = True
            call.timer.start()
            page = self._page(target, call)
            self._remaining(call)
            return {'url': page['final_url'], 'fetched_at': page['fetched_at'], 'text': page['text'],
                    'selection': 'relevant_excerpt',
                    'sha256': hashlib.sha256(page['text'].encode('utf-8')).hexdigest()}
        except Exception as error:
            try:
                self._remaining(call)
            except WebSourceError as terminal:
                error = terminal
            if isinstance(error, WebSearchError):
                raise error from None
            code = error.code if isinstance(error, WebSourceError) else (
                'timeout' if isinstance(error, TimeoutError) else 'unavailable')
            raise WebSearchError(code) from None
        finally:
            if call.timer is not None:
                call.timer.cancel()
            with self._lock:
                if self._active is call:
                    self._active = None
