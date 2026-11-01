"""Explicit connection lifecycle around the existing, owned ChatSession consumer."""
from __future__ import annotations

import re
import threading

from pacttrail.application.chat import PROMPTS, chat_error
from pacttrail.application import ordinary_chat


class LocalModelChat:
    """One process budget; factories are trusted composition, never user input."""

    def __init__(self, make_session, make_completion, usage_status, *, answer_mode='evidence'):
        if (not all(callable(value) for value in (make_session, make_completion, usage_status))
                or type(answer_mode) is not str or answer_mode not in ('evidence', 'ordinary')):
            raise chat_error('invalid_request')
        self._answer_mode = answer_mode
        self._make_session, self._make_completion = make_session, make_completion
        self._usage_status = usage_status
        self._lock = threading.RLock()
        self._session = make_session()
        self._transport = None
        self._draining = []
        self._phase = 'unconfigured'
        self._armed = self._verified = self._locked = self._closed = False
        self._revision = self._requests_total = 0
        self._requests = {}

    def _busy(self, state):
        # A closed HTTP worker may still be unwinding after its caller returned.
        self._draining = [(session, transport) for session, transport in self._draining
                          if session.status()['busy'] or
                          (transport is not None and transport.status()['busy'])]
        return (state['busy'] or bool(self._draining) or
                (self._transport is not None and self._transport.status()['busy']))

    def _retire(self):
        self._session.close()  # ChatSession owns the cleanup-once guarantee.
        self._armed = False
        if self._usage_status()['calls']:
            self._locked = True

    def _observe(self):
        state = self._session.status()
        usage = self._usage_status()
        operation = state['operation']
        if self._transport is not None and not self._closed:
            # Protocol accounting can stop before its worker commits the actual
            # error/refusal. Closing a still-running consumer here would replace
            # that outcome with a generic stop. Admission already denies sends.
            running = operation is not None and operation['status'] == 'running'
            failed = ((usage['stopped'] and not running) or
                      (operation is not None and operation['status'] in ('error', 'stopped')))
            if failed:
                self._phase = 'failed'
                self._retire()
                state = self._session.status()
            elif operation is not None and operation['status'] == 'complete':
                self._verified = True
        return state, usage

    def _project(self):
        state, usage = self._observe()
        busy = bool(self._busy(state))
        connected = self._transport is not None
        can_send = (connected and self._armed and not self._closed and not self._locked
                    and self._phase == 'ready' and not busy and not usage['stopped']
                    and usage['cost_known'] and usage['calls'] < 3)
        state.update(revision=self._revision, busy=busy, live_available=bool(can_send))
        if self._answer_mode == 'ordinary':
            # Selected product lane stays explicit even before configuration or after
            # clearing. A disconnected ordinary lane must never look like a fallback.
            state.update(mode='local_model', provider_id='deepseek',
                         prompts=list(ordinary_chat.PROMPTS[:3]))
        elif connected:
            state.update(mode='local_model', provider_id='deepseek', prompts=list(PROMPTS[:3]))
        state['connection'] = {
            'state': self._phase, 'can_send': bool(can_send), 'verified': self._verified,
            'model': 'deepseek-flash', 'endpoint': 'https://api.deepseek.com/chat/completions',
            'timeout_seconds': 60, 'calls': usage['calls'], 'max_calls': 3,
            'budget_nano_usd': usage['budget_nano_usd'],
            'accounted_nano_usd': usage['accounted_nano_usd'],
            'reserved_nano_usd': usage['reserved_nano_usd'], 'cost_known': usage['cost_known'],
            'stopped': bool(usage['stopped'] or self._locked),
            'stop_reason': usage['stop_reason'] or ('provider_unavailable' if self._locked else None),
            'input_estimate_tokens': usage['input_estimate_tokens'], 'usage': usage['usage'],
            'token_basis': usage['token_basis'], 'pricing_basis': usage['pricing_basis'],
        }
        return state

    def status(self):
        with self._lock:
            return self._project()

    def _bound(self, body, state):
        if (type(body['conversation_id']) is not str or
                re.fullmatch(r'[a-f0-9]{32}', body['conversation_id']) is None or
                type(body['revision']) is not int or not 0 <= body['revision'] <= 2147483647):
            raise chat_error('invalid_request')
        if (body['conversation_id'], body['revision']) != (state['conversation_id'], self._revision):
            raise chat_error('stale_request')

    def _replace(self, session, transport=None):
        old, old_transport = self._session, self._transport
        self._retire()
        self._draining.append((old, old_transport))
        self._session, self._transport = session, transport
        self._revision += 1
        self._requests.clear()
        self._armed = self._verified = False

    def _clear(self, provider='offline'):
        fresh = self._make_session()
        try:
            if provider != 'offline':
                state = fresh.status()
                fresh.action('/reset', {'conversation_id': state['conversation_id'],
                                       'revision': state['revision'], 'provider_id': provider})
            self._replace(fresh)
        except Exception:
            fresh.close()
            raise
        self._phase = 'cleared'

    def _configure(self, key, state):
        if (type(key) is not str or not 1 <= len(key) <= 512 or
                any(not 33 <= ord(char) <= 126 for char in key)):
            raise chat_error('invalid_request')
        if state['busy']:
            raise chat_error('busy')
        if self._transport is not None:
            raise chat_error('request_conflict')
        if state['connection']['stopped'] or state['connection']['calls'] >= 3:
            raise chat_error('provider_unavailable')
        transport = fresh = None
        try:
            completion, transport = self._make_completion(key)
            completion_field = 'ordinary_complete' if self._answer_mode == 'ordinary' else 'complete'
            fresh = self._make_session(**{completion_field: completion},
                                       cancel_completion=transport.close, completion_timeout=60)
            self._replace(fresh, transport)
            self._phase = 'configured_unverified'
        except Exception:
            if fresh is not None:
                fresh.close()
            elif transport is not None:
                try:
                    transport.close()
                except Exception:
                    pass
            self._phase = 'failed'
            raise chat_error('provider_unavailable') from None

    def action(self, path, body):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            fields = {
                '/send': {'request_id', 'message'}, '/stop': {'request_id'},
                '/reset': {'provider_id'}, '/connection': {'action'},
            }
            if type(path) is not str or path not in fields or type(body) is not dict:
                raise chat_error('invalid_request')
            extra = {'key'} if path == '/connection' and body.get('action') == 'configure' else set()
            if set(body) != {'conversation_id', 'revision'} | fields[path] | extra:
                raise chat_error('invalid_request')
            state = self._project()
            self._bound(body, state)
            if path == '/connection':
                action = body['action']
                if type(action) is not str or action not in ('configure', 'activate', 'clear'):
                    raise chat_error('invalid_request')
                if action == 'configure':
                    self._configure(body['key'], state)
                elif action == 'clear':
                    self._clear()
                else:
                    if state['busy']:
                        raise chat_error('busy')
                    if self._phase != 'configured_unverified' or self._transport is None or self._locked:
                        raise chat_error('provider_unavailable')
                    self._armed, self._phase = True, 'ready'
                    self._revision += 1
                return self._project()
            if path == '/reset':
                if (type(body['provider_id']) is not str or
                        body['provider_id'] not in {p['id'] for p in state['providers']} or
                        (self._answer_mode == 'ordinary' and body['provider_id'] != 'offline')):
                    raise chat_error('invalid_request')
                self._clear(body['provider_id'])
                return self._project()
            if (type(body['request_id']) is not str or
                    re.fullmatch(r'[a-f0-9]{32}', body['request_id']) is None):
                raise chat_error('invalid_request')
            inner = {**body, 'revision': self._session.status()['revision']}
            if path == '/send':
                if body['request_id'] in self._requests:
                    if self._requests[body['request_id']] != body:
                        raise chat_error('request_conflict')
                    return self._project()
                if state['busy']:
                    raise chat_error('busy')
                if self._requests_total >= 24:
                    raise chat_error('request_limit')
                if self._transport is not None or self._answer_mode == 'ordinary':
                    if state['connection']['calls'] >= 3:
                        raise chat_error('completion_request_limit')
                    if not state['connection']['can_send']:
                        raise chat_error('provider_unavailable')
                    if (self._answer_mode == 'evidence' and
                            body['message'] != PROMPTS[state['connection']['calls']]):
                        raise chat_error('sample_only')
                elif self._locked and state['provider_id'] != 'offline':
                    raise chat_error('provider_unavailable')
            try:
                self._session.action(path, inner)
            except Exception:
                self._observe()
                raise
            if path == '/send':
                self._requests[body['request_id']] = dict(body)
                self._requests_total += 1
            return self._project()

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._retire()
                if self._transport is not None:
                    self._phase = 'cleared'
