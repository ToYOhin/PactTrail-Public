"""Fixed HTTPS transport for explicit active product connections.

Tokens must be supplied explicitly by the connection composition. Tests use
only synthetic tokens and in-memory connections. This module never finds keys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import math
import socket
import ssl
import threading
import time

from pacttrail.application.model_evidence import _safe_error, model_evidence_error


_ENDPOINT = 'https://api.deepseek.com/chat/completions'


@dataclass
class _Call:
    started: float
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)
    sock: object = None
    result: object = None
    error: str | None = None


def _seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= 60


class DeepSeekHTTPTransport:
    """One physical operation at a time, even after the caller stops waiting."""

    def __init__(self, token=None, *, connection_factory=None, clock=time.monotonic,
                 max_wait_seconds=60):
        if ((token is not None and (type(token) is not str or not 1 <= len(token) <= 512
                                    or any(not 33 <= ord(c) <= 126 for c in token)))
                or (connection_factory is not None and not callable(connection_factory))
                or not callable(clock) or not _seconds(max_wait_seconds)):
            raise model_evidence_error('invalid_request')
        self._token = token
        self._factory = http.client.HTTPSConnection if connection_factory is None else connection_factory
        self._clock, self._max_wait = clock, max_wait_seconds
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
                raise ValueError('clock')
            return value
        except Exception:
            raise model_evidence_error('provider_unavailable') from None

    def _remaining(self, call):
        now = self._now()
        if now < call.started:
            raise model_evidence_error('provider_unavailable')
        if call.cancel.is_set() or now >= call.deadline:
            raise TimeoutError('timeout')
        return call.deadline - now

    def _socket_timeout(self, call):
        remaining = self._remaining(call)
        if call.sock is None:
            raise model_evidence_error('provider_unavailable')
        call.sock.settimeout(remaining)

    @staticmethod
    def _cancel(call):
        call.cancel.set()
        call.done.set()
        if call.sock is not None:
            try:
                call.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass

    def close(self):
        with self._lock:
            self._closed, self._token = True, None
            call = self._active
        if call is not None:
            self._cancel(call)

    def __call__(self, endpoint, body, timeout_seconds):
        if (type(endpoint) is not str or endpoint != _ENDPOINT
                or type(body) is not bytes or not 1 <= len(body) <= 163840
                or not _seconds(timeout_seconds)):
            raise model_evidence_error('invalid_request')
        if len(body) > 32768:
            # Large requests belong only to a strictly validated, explicitly bound
            # full-history conversation. Other HTTP consumers keep their old cap.
            from pacttrail.platform.deepseek_tool_request import (
                conversation_capacity_from_system, validate_request,
            )
            try:
                request = validate_request(body)
                if conversation_capacity_from_system(request['messages'][0]['content']) is None:
                    raise ValueError('missing conversation capacity')
            except Exception:
                raise model_evidence_error('invalid_request') from None
        with self._lock:
            if self._closed or self._active is not None:
                raise model_evidence_error('provider_unavailable')
            if self._token is None:
                raise model_evidence_error('provider_auth')
            started = self._now()
            call = _Call(started, started + min(timeout_seconds, self._max_wait))
            self._active = call
            worker = threading.Thread(target=self._run, args=(call, self._token, body),
                                      daemon=True, name='pacttrail-deepseek-http')
            try:
                worker.start()
            except Exception:
                self._active = None
                raise model_evidence_error('provider_unavailable') from None
        try:
            if not call.done.wait(self._remaining(call)):
                raise TimeoutError('timeout')
            with self._lock:
                closed = self._closed
            if closed:
                raise model_evidence_error('provider_unavailable')
            self._remaining(call)
            if call.error == 'timeout':
                raise TimeoutError('timeout')
            if call.error is not None:
                raise model_evidence_error(call.error)
            if call.result is None:
                raise model_evidence_error('provider_unavailable')
            return call.result
        except TimeoutError:
            self._cancel(call)
            raise TimeoutError('timeout') from None
        except Exception as error:
            self._cancel(call)
            raise _safe_error(error, 'provider_unavailable') from None

    def _read(self, response, call):
        status = response.status
        if type(status) is not int or not 100 <= status <= 599:
            raise model_evidence_error('invalid_response')
        if status != 200:
            return status, b''
        content_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
        encoding = response.getheader('Content-Encoding', 'identity').strip().lower()
        length = response.getheader('Content-Length')
        transfer = response.getheader('Transfer-Encoding')
        if (content_type != 'application/json' or encoding not in ('', 'identity')
                or (transfer is not None and (transfer.strip().lower() != 'chunked' or length is not None))):
            raise model_evidence_error('invalid_response')
        expected = None
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 10:
                raise model_evidence_error('invalid_response')
            expected = int(length)
            if expected > 65536:
                raise model_evidence_error('response_limit')
        chunks, total = [], 0
        while True:
            # HTTPResponse can close the last socket reference as soon as the
            # declared body ends. Do not change a closed socket's timeout.
            if response.isclosed():
                break
            self._socket_timeout(call)
            chunk = response.read1(min(8192, 65537 - total))
            self._remaining(call)
            if type(chunk) is not bytes:
                raise model_evidence_error('invalid_response')
            if not chunk:
                break
            total += len(chunk)
            if total > 65536:
                raise model_evidence_error('response_limit')
            chunks.append(chunk)
        if expected is not None and total != expected:
            raise model_evidence_error('invalid_response')
        return status, b''.join(chunks)

    def _run(self, call, token, body):
        connection = response = None
        try:
            context = ssl.create_default_context()
            context.set_alpn_protocols(['http/1.1'])
            connection = self._factory('api.deepseek.com', 443,
                                       timeout=self._remaining(call), context=context)
            connection.set_debuglevel(0)
            self._remaining(call)
            connection.connect()
            call.sock = connection.sock
            self._socket_timeout(call)
            connection.request('POST', '/chat/completions', body=body, headers={
                'Authorization': 'Bearer ' + token,
                'Content-Type': 'application/json', 'Accept': 'application/json',
                'Accept-Encoding': 'identity', 'Connection': 'close',
            }, encode_chunked=False)
            self._socket_timeout(call)
            response = connection.getresponse()
            self._remaining(call)
            call.result = self._read(response, call)
        except TimeoutError:
            call.error = 'timeout'
        except http.client.HTTPException:
            call.error = 'invalid_response'
        except Exception as error:
            call.error = _safe_error(error, 'provider_unavailable').args[0].removeprefix('model_evidence_error: ')
        finally:
            for resource in (response, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        call.result = None
                        call.error = call.error or 'provider_unavailable'
            with self._lock:
                if self._active is call:
                    self._active = None
            call.done.set()
