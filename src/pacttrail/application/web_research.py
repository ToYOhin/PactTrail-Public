"""One opt-in acquisition worker; it never submits a model or attaches a task."""
from __future__ import annotations

import copy
import re
import threading
import time

from pacttrail.application.chat import chat_error
from pacttrail.application.chat_project import from_web_pages


class WebResearch:
    def __init__(self, factory, *, transport='synthetic', clock=time.monotonic):
        if not callable(factory) or transport not in ('http', 'synthetic') or not callable(clock):
            raise chat_error('invalid_request')
        self._factory, self._transport, self._clock = factory, transport, clock
        self._lock = threading.RLock()
        self._worker = self._adapter = self._operation = self._preview = None
        self._cancel = threading.Event()
        self._seen = {}
        self._epoch, self._deadline, self._closed = 0, 0, False

    def _busy(self):
        return self._worker is not None and self._worker.is_alive()

    def _retire(self):
        self._epoch += 1
        self._cancel.set()
        self._preview = None
        if self._adapter is not None:
            try:
                self._adapter.close()
            except Exception:
                pass

    def _expire(self):
        if (self._operation is not None and self._operation['status'] == 'running'
                and self._clock() >= self._deadline):
            self._retire()
            self._operation.update(status='error', error='timeout')

    def project(self):
        with self._lock:
            self._expire()
            busy = self._busy()
            operation, preview = copy.deepcopy(self._operation), copy.deepcopy(self._preview)
            # Cleanup finishes before publication, including the brief thread-return interval.
            if operation and operation['status'] == 'ready' and busy:
                operation['status'], preview = 'running', None
            return dict(version=1, transport=self._transport, operation=operation, preview=preview,
                        busy=busy, remaining_fetches=6-len(self._seen))

    def blocks_task(self):
        value = self.project()
        return value['busy'] or value['preview'] is not None

    def fetch(self, fetch_id, urls):
        if (type(fetch_id) is not str or re.fullmatch('[a-f0-9]{32}', fetch_id) is None
                or type(urls) is not list or not 1 <= len(urls) <= 3
                or any(type(url) is not str or not 1 <= len(url) <= 2048 for url in urls)):
            raise chat_error('invalid_request')
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            if fetch_id in self._seen:
                if self._seen[fetch_id] != urls:
                    raise chat_error('request_conflict')
                return
            if self.blocks_task():
                raise chat_error('busy')
            if len(self._seen) >= 6:
                raise chat_error('request_limit')
            self._seen[fetch_id] = list(urls)
            self._epoch += 1
            self._cancel = threading.Event()
            self._deadline = self._clock() + 15
            self._preview = None
            self._operation = dict(id=fetch_id, status='running', error=None)
            self._worker = threading.Thread(target=self._run,
                args=(self._epoch, list(urls), self._cancel), daemon=True, name='pacttrail-web')
            try:
                self._worker.start()
            except Exception:
                self._operation.update(status='error', error='unavailable')
                self._worker = None

    def _run(self, epoch, urls, cancel):
        adapter, preview, error = None, None, None
        try:
            adapter = self._factory()
            with self._lock:
                if epoch != self._epoch or cancel.is_set():
                    return
                self._adapter = adapter
            pages = adapter.fetch(urls, timeout_seconds=15, cancel=cancel)
            preview = from_web_pages(pages, self._transport)
        except Exception as failure:
            from pacttrail.domain.web_source import WebSourceError
            if isinstance(failure, WebSourceError) and failure.code in {
                    'invalid_url', 'forbidden_address', 'invalid_response', 'response_limit',
                    'redirect_limit', 'timeout', 'unavailable'}:
                error = failure.code
            elif str(failure) in ('chat_error: material_limit', 'chat_error: response_limit'):
                error = 'response_limit'
            elif str(failure) == 'chat_error: material_invalid':
                error = 'invalid_response'
            else:
                error = 'unavailable'
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except Exception:
                    error = 'unavailable'
            with self._lock:
                if self._adapter is adapter:
                    self._adapter = None
                self._expire()
                if epoch == self._epoch and not cancel.is_set():
                    if error is not None or preview is None:
                        self._operation.update(status='error', error=error or 'unavailable')
                    else:
                        self._preview = preview
                        self._operation.update(status='ready', error=None)

    def cancel(self, fetch_id):
        with self._lock:
            if self._operation is None or type(fetch_id) is not str or fetch_id != self._operation['id']:
                raise chat_error('stale_request')
            self._retire()
            self._operation.update(status='cancelled', error=None)

    def attach(self, fetch_id, digest):
        with self._lock:
            self._expire()
            if self._busy():
                raise chat_error('busy')
            if (self._preview is None or self._operation is None
                    or fetch_id != self._operation['id'] or digest != self._preview['sha256']
                    or type(digest) is not str or type(fetch_id) is not str):
                raise chat_error('stale_request')
            result = copy.deepcopy(self._preview)
            self._preview = self._operation = None
            return result

    def clear(self):
        with self._lock:
            self._retire()
            self._operation = None

    def reject_preview(self):
        """Keep existing task facts readable when their combined view is full."""
        with self._lock:
            if self._preview is not None:
                self._retire()
                self._operation.update(status='error', error='response_limit')

    def close(self):
        with self._lock:
            self._closed = True
            self.clear()
