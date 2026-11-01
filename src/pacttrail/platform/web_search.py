"""One bounded Bing public RSS search; no keys, proxies, paging or retries."""
from __future__ import annotations

import json
import math
import re
import threading
import time
from urllib.parse import urlencode
from xml.etree import ElementTree

from pacttrail.domain.web_search import WebSearchError, make_query, validate_search
from pacttrail.domain.web_source import WebSourceError, validate_url
from pacttrail.platform.web_fetch import WebFetcher, _Call, _Text, _resolve


_ENDPOINT = 'https://cn.bing.com/search'


def create_search_research():
    from pacttrail.application.search_research import SearchResearch
    from pacttrail.platform.search_page import SearchPageReader
    return SearchResearch(WebSearcher, SearchPageReader)


def _plain(value, maximum):
    parser = _Text()
    parser.feed(value)
    parser.close()
    text = ' '.join(parser.text().split())
    if any(ord(char) < 32 or 127 <= ord(char) <= 159
           or 0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise WebSearchError()
    return text[:maximum]


def _results(raw):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise WebSearchError()
    try:
        text = raw.decode('utf-8')
        declaration = re.match(r'\ufeff?\s*<\?xml\s+([^?]*)\?>', text, re.I)
        if declaration is not None:
            encoding = re.search(r'encoding\s*=\s*[\'"]([^\'"]+)[\'"]', declaration[1], re.I)
            if encoding is not None and encoding[1].lower() not in ('utf-8', 'utf8'):
                raise ValueError()
        tree = ElementTree.fromstring(text)
        if tree.tag != 'rss' or len(tree.findall('channel')) != 1 or sum(1 for _ in tree.iter()) > 512:
            raise ValueError()
        items = tree.findall('./channel/item')
        if len(items) > 50:
            raise WebSearchError('response_limit')
        result, seen = [], set()
        for item in items:
            if any(len(item.findall(name)) != 1 for name in ('title', 'link', 'description')):
                continue
            try:
                target = validate_url((item.findtext('link') or '').strip())
                if (target['host'] == 'bing.com' or target['host'].endswith('.bing.com')
                        or target['url'] in seen):
                    continue
                title = _plain(item.findtext('title') or '', 160)
                snippet = _plain(item.findtext('description') or '', 600)
                if not title:
                    continue
            except (WebSourceError, WebSearchError):
                continue
            seen.add(target['url'])
            result.append({'title': title, 'url': target['url'], 'snippet': snippet})
            if len(result) == 3:
                return result
        if not result:
            raise WebSearchError('empty')
        return result
    except WebSearchError:
        raise
    except (ElementTree.ParseError, UnicodeError, ValueError, TypeError):
        raise WebSearchError() from None


class WebSearcher(WebFetcher):
    """Reuse the page transport's pinned TLS, redirects and address validation.

    ``deadline`` is an absolute monotonic deadline. Each adapter permits one
    search; the caller owns run-level admission. A DNS worker only resolves the
    fixed endpoint or a validated redirect and cannot initiate an HTTP request.
    """

    def __init__(self, *, resolver=None, connection_factory=None, clock=time.monotonic, utc_now=None):
        self._search_resolver = _resolve if resolver is None else resolver
        self._used = False
        try:
            if not callable(self._search_resolver):
                raise WebSourceError()
            super().__init__(resolver=self._bounded_resolve, connection_factory=connection_factory,
                             clock=clock, utc_now=utc_now)
        except WebSourceError:
            raise WebSearchError() from None

    def _bounded_resolve(self, host, port):
        call, done, answer = self._active, threading.Event(), []
        def resolve():
            try:
                answer.append((True, self._search_resolver(host, port)))
            except Exception:
                answer.append((False, None))
            finally:
                done.set()
        worker = threading.Thread(target=resolve, daemon=True, name='pacttrail-search-dns')
        worker.start()
        while not done.wait(min(.05, self._remaining(call))):
            self._remaining(call)
        self._remaining(call)
        if not answer or not answer[0][0]:
            raise WebSourceError()
        return answer[0][1]

    def _body(self, response, headers, call):
        if (re.fullmatch(r'(text/xml|application/xml|application/rss\+xml)(?:\s*;\s*charset\s*=\s*(?:"utf-8"|utf-8))?',
                         headers.get('content-type', ''), re.I) is None
                or headers.get('content-encoding', 'identity').lower() != 'identity'):
            raise WebSearchError()
        transfer, length = headers.get('transfer-encoding'), headers.get('content-length')
        if transfer is not None and (transfer.lower() != 'chunked' or length is not None):
            raise WebSearchError()
        expected = None
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 10:
                raise WebSearchError()
            expected = int(length)
            if expected > 65536:
                raise WebSearchError('response_limit')
        chunks, total = [], 0
        while not response.isclosed():
            call.sock.settimeout(self._remaining(call))
            chunk = response.read1(min(8192, 65537 - total))
            self._remaining(call)
            if type(chunk) is not bytes:
                raise WebSearchError()
            if not chunk:
                break
            total += len(chunk)
            if total > 65536:
                raise WebSearchError('response_limit')
            chunks.append(chunk)
        if expected is not None and total != expected:
            raise WebSearchError()
        return 'application/rss+xml', json.dumps(_results(b''.join(chunks)), ensure_ascii=False)

    def search(self, query, *, cancel, deadline):
        if (type(query) is not str or make_query(query) != query
                or not isinstance(cancel, threading.Event)
                or type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise WebSearchError()
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
            call = _Call(started, min(deadline, started + 8), cancel)
            self._active = call
        try:
            self._remaining(call)
            call.timer = threading.Timer(self._remaining(call), self._deadline_expired, args=(call,))
            call.timer.daemon = True
            call.timer.start()
            target = validate_url(_ENDPOINT)
            target['target'] += '?' + urlencode({'format': 'rss', 'q': query})
            target['url'] = 'https://' + target['host'] + target['target']
            page = self._page(target, call)
            self._remaining(call)
            return validate_search({'version': 1, 'query': query, 'searched_at': page['fetched_at'],
                                    'provider': 'bing', 'transport': 'http',
                                    'results': json.loads(page['text'])})
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
