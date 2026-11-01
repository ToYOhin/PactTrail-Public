"""Explicit process-owned connection around the existing bounded tool consumer."""
from __future__ import annotations

import copy
import re
import time

from pacttrail.application.chat import chat_error
from pacttrail.application.tool_task import ToolTaskSession


class LocalToolModelChat:
    """One active key and cumulative budget; every task owns its own completion.

    Factories are trusted composition. They are called only for an explicit send,
    never configuration or status. The owner and consumer share one reentrant
    lock, so worker retirement cannot invert the controller/session lock order.
    """

    @property
    def response_byte_limit(self):
        return self._session.response_byte_limit

    def __init__(self, files, make_completion, usage_status, *, transport_mode,
                 clock=time.monotonic, delay_seconds=0.05, followup_store=None,
                 web_fetcher_factory=None, web_transport='synthetic', preview_request=None,
                 make_search=None, resume_admission=None):
        if (not callable(make_completion) or not callable(usage_status)
                or type(transport_mode) is not str or transport_mode not in ('live', 'synthetic')):
            raise chat_error('invalid_request')
        self._make_completion, self._usage_status = make_completion, usage_status
        if make_search is not None and not callable(make_search):
            raise chat_error('invalid_request')
        self._make_search = make_search
        if resume_admission is not None and not callable(resume_admission):
            raise chat_error('invalid_request')
        self._resume_admission = resume_admission
        self._transport_mode = transport_mode
        self._key = None
        self._key_store = None
        self._phase = 'unconfigured'
        self._armed = self._verified = self._locked = self._closed = False
        self._revision = self._requests_total = 0
        self._requests = {}
        self._activation_operation = None
        self._transports = []
        self._transport_runs = {}
        self._run_resource_samples = {}
        self._session = ToolTaskSession(
            files, decision_factory=self._make_decider, model_status=usage_status,
            clock=clock, delay_seconds=delay_seconds, completion_timeout=60,
            recording_mode='synthetic_protocol' if transport_mode == 'synthetic' else 'model_protocol',
            followup_store=followup_store, web_fetcher_factory=web_fetcher_factory, web_transport=web_transport,
            preview_request=preview_request, on_retired=self._retired)
        self._lock = self._session.coordination_lock

    def enable_web_search(self, factory):
        with self._session.coordination_lock:
            if self._make_search is None:
                self._session.enable_web_search(factory)
            else:
                self._session.enable_web_search(self._make_searcher, provider='deepseek', paid=True)

    def _make_searcher(self, *, before_dispatch, after_dispatch):
        if self._key is None or not self._armed or self._phase != 'ready' or self._closed:
            raise chat_error('provider_unavailable')
        revision, operation = self._revision, self._session.control_state().run

        def register(adapter):
            with self._lock:
                self._transports.append(adapter)
                self._transport_runs[id(adapter)] = operation.run_id
                if (revision != self._revision or not self._session.is_current_run(operation)
                        or operation.cancelled or self._closed or not self._armed or self._key is None
                        or not callable(getattr(adapter, 'status', None))):
                    self._cleanup(None, adapter)
                    raise chat_error('provider_unavailable')

        return self._make_search(self._key, before_dispatch=before_dispatch,
                                 after_dispatch=after_dispatch, register=register)

    def enable_key_store(self, store):
        """Explicit main-entry opt-in; restore only this workspace's saved key."""
        with self._lock:
            if (self._closed or not self._session.control_state().persistent or self._key is not None
                    or self._key_store is not None
                    or not all(callable(getattr(store, name, None)) for name in ('load', 'save', 'clear'))):
                raise chat_error('invalid_request')
            try:
                key = store.load()
                if key is not None:
                    self._configure(key, self._project(), persist=False)
                    self._armed, self._phase = True, 'ready'
                    self._revision += 1
            except Exception:
                raise chat_error('state_unavailable') from None
            self._key_store = store
            return self

    def enable_conversation(self, conversation, *, ordinary_capacity=None):
        with self._lock:
            self._session.enable_conversation(conversation, ordinary_capacity=ordinary_capacity)
            self._revision = max(self._revision, self._session.control_state().revision)
            return self

    def read_binding(self):
        """Observe identity and conservative activity without driving lifecycle."""
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            binding = self._session.read_binding()
            busy = binding['busy']
            for transport in self._transports:
                try:
                    state = transport.status()
                    if (type(state) is not dict or type(state.get('busy')) is not bool
                            or type(state.get('closed')) is not bool):
                        raise ValueError('transport state')
                    busy = busy or state['busy'] or not state['closed']
                except Exception:
                    busy = True
            return dict(conversation_id=binding['conversation_id'], revision=self._revision,
                        busy=bool(busy))

    def set_project_reader(self, reader, *, target_root=None):
        with self._lock:
            return self._session.set_project_reader(reader, target_root=target_root)

    def set_artifact_reference_reader(self, reader):
        with self._lock:
            self._session.set_artifact_reference_reader(reader)
            return self

    def artifact_reference_action(self, body, reference=None):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            state = self._project()
            self._bound(body, state)
            if state['busy']:
                raise chat_error('busy')
            self._session.artifact_reference_action({**body, 'revision': self._session.control_state().revision}, reference)
            self._revision += 1
            return self._project()

    def artifact_lineage_for_record(self, record):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            return self._session.artifact_lineage_for_record(record)

    def prepare_application(self, run_id, digest):
        with self._lock:
            previous = self._session.control_state().revision
            result = self._session.prepare_application(run_id, digest)
            if self._session.control_state().revision != previous:
                self._revision += 1
            return result

    def record_application(self, receipt):
        with self._lock:
            return self._session.record_application(receipt)

    def _retire(self, phase):
        # Explicit clear/close drop the key before invoking cleanup callbacks.
        # A persistent conversation retains configuration after failure, while
        # the same stopped/unknown budget and physical-worker guards still apply.
        if phase != 'failed' or not self._session.control_state().persistent:
            self._key = None
        self._armed = self._verified = False
        if self._usage_status()['calls']:
            self._locked = True
        self._phase = phase

    def _retired(self, operation):
        # Called under the shared lock, including error/timeout paths in workers.
        # A protocol finish alone is insufficient: the consumer verifies its file.
        if (self._closed or self._key is None or operation.run_id is None
                or not self._session.is_current_run(operation)
                or operation.same_operation(self._activation_operation)
                or not self._armed or self._phase != 'ready'):
            return
        if operation.status == 'complete':
            self._verified = True
        elif operation.status in ('error', 'stopped'):
            self._retire('failed')

    def _physical_busy(self):
        remaining, busy, samples = [], False, {}
        for transport in self._transports:
            run_id = self._transport_runs.get(id(transport))
            try:
                state = transport.status()
                if (type(state) is not dict or type(state.get('busy')) is not bool
                        or type(state.get('closed')) is not bool):
                    raise ValueError('transport state')
            except Exception:
                samples.setdefault(run_id, []).append('unknown')
                # An unobservable worker must not permit an overlapping dispatch.
                self._retire('failed' if not self._closed else 'cleared')
                self._session.interrupt_current(self._session.control_state().run,
                                                error='provider_unavailable')
                remaining.append(transport)
                busy = True
                continue
            samples.setdefault(run_id, []).append(
                'active' if state['busy'] or not state['closed'] else 'released')
            if state['busy'] or not state['closed']:
                remaining.append(transport)
            busy = busy or state['busy']
        self._transports = remaining
        self._transport_runs = {id(item): self._transport_runs.get(id(item)) for item in remaining}
        self._run_resource_samples = samples
        return busy

    @staticmethod
    def _cleanup(completion, transport):
        # An accepted completion owns transport cleanup; never close both.
        try:
            close = getattr(completion, 'close', None)
            if not callable(close):
                close = getattr(transport, 'close', None)
            if callable(close):
                close()
        except Exception:
            pass

    def _make_decider(self, run_id, deadline, cancel):
        if self._key is None or not self._armed or self._phase != 'ready' or self._closed:
            raise chat_error('provider_unavailable')
        revision = self._revision
        completion = transport = None
        try:
            completion, transport = self._make_completion(self._key, run_id, deadline, cancel)
            if (not callable(completion) or completion.completion_timeout != 60
                    or not callable(getattr(completion, 'close', None))
                    or not callable(transport) or not callable(getattr(transport, 'close', None))
                    or not callable(getattr(transport, 'status', None))):
                raise ValueError('completion')
            # Retain physical-worker identity even if a trusted factory re-enters
            # clear/close; the resulting worker must still drain before reuse.
            self._transports.append(transport)
            self._transport_runs[id(transport)] = run_id
            if (revision != self._revision or self._key is None or not self._armed
                    or self._closed or cancel.is_set()):
                raise ValueError('retired')
            return completion
        except Exception:
            self._cleanup(completion, transport)
            raise chat_error('provider_unavailable') from None

    def _project(self):
        state = self._session.status()
        previous_phase = self._phase
        physical_busy = self._physical_busy()
        if self._phase != previous_phase:
            state = self._session.status()
        busy = physical_busy or state['busy']
        usage = copy.deepcopy(self._usage_status())
        can_send = (self._key is not None and self._armed and not self._closed and not self._locked
                    and self._phase == 'ready' and not busy and not usage['stopped']
                    and usage['cost_known'] and usage['calls'] < 48)
        state.update(mode='agent_model', provider_id='deepseek', revision=self._revision,
                     busy=bool(busy), live_available=bool(can_send))
        state['task'].update(version=3, decision_mode='model_protocol', model=usage)
        state['task']['budget'].update(model_dispatches=usage['calls'],
                                      model_cost_nano=usage.get('model_accounted_nano_usd', usage['accounted_nano_usd']))
        state['connection'] = {
            'state': self._phase, 'can_send': bool(can_send), 'verified': self._verified,
            'transport_mode': self._transport_mode,
            'model': 'deepseek-flash', 'endpoint': 'https://api.deepseek.com/chat/completions',
            'timeout_seconds': 60, 'calls': usage['calls'], 'max_calls': 48,
            'budget_nano_usd': usage['budget_nano_usd'],
            'accounted_nano_usd': usage['accounted_nano_usd'],
            'reserved_nano_usd': usage['reserved_nano_usd'], 'cost_known': usage['cost_known'],
            'stopped': bool(usage['stopped'] or self._locked),
            'stop_reason': usage['stop_reason'] or ('provider_unavailable' if self._locked else None),
            'input_estimate_tokens': usage['input_estimate_tokens'], 'usage': usage['usage'],
            'token_basis': usage['token_basis'], 'pricing_basis': usage['pricing_basis'],
        }
        if self._session.control_state().persistent:
            rearmable = self._rearmable(usage, busy)
            state['connection'].update(
                can_reactivate=bool(self._phase == 'failed' and self._key is not None and rearmable),
                can_configure=bool(self._phase in ('unconfigured', 'cleared') and self._key is None
                    and (rearmable or not busy and not self._locked and not usage['stopped']
                         and not self._closed and not self._transports and usage['cost_known']
                         and usage['reserved_nano_usd'] == 0 and usage['calls'] < 48
                         and self._requests_total < 24
                         and usage['accounted_nano_usd'] < usage['budget_nano_usd'])))
        for field in ('ledger_version', 'search_calls', 'search_pricing_basis',
                      'model_accounted_nano_usd', 'search_accounted_nano_usd',
                      'model_reserved_nano_usd', 'search_reserved_nano_usd'):
            if field in usage:
                state['connection'][field] = usage[field]
        run_id = (state.get('operation') or {}).get('id')
        return self._session.finalize_projection(state,
            resources=self._run_resource_samples.get(run_id, ()))

    def status(self):
        with self._lock:
            return self._project()

    def _bound(self, body, state):
        if (type(body['conversation_id']) is not str
                or re.fullmatch(r'[a-f0-9]{32}', body['conversation_id']) is None
                or type(body['revision']) is not int or not 0 <= body['revision'] <= 2147483647):
            raise chat_error('invalid_request')
        if (body['conversation_id'], body['revision']) != (state['conversation_id'], self._revision):
            raise chat_error('stale_request')

    def _rearmable(self, usage, busy):
        return (self._session.control_state().persistent and self._resume_admission is not None
                and not self._closed and not busy and not self._transports
                and usage['cost_known'] and usage['reserved_nano_usd'] == 0
                and usage['calls'] < 48 and self._requests_total < 24
                and usage['accounted_nano_usd'] < usage['budget_nano_usd'])

    def _resume_known(self):
        # Called only from an explicit configure/activate, after the current
        # physical-state projection. Neither status nor clear releases a stop.
        usage = self._usage_status()
        if not self._rearmable(usage, self._session.control_state().worker_busy):
            raise chat_error('provider_unavailable')
        try:
            accepted = self._resume_admission()
        except Exception:
            accepted = False
        if accepted is not True:
            raise chat_error('provider_unavailable')
        self._locked = False

    def _configure(self, key, state, *, persist=True):
        if (type(key) is not str or not 1 <= len(key) <= 512
                or any(not 33 <= ord(char) <= 126 for char in key)):
            raise chat_error('invalid_request')
        if state['busy']:
            raise chat_error('busy')
        if self._key is not None:
            raise chat_error('request_conflict')
        if (self._session.control_state().persistent
                and not state['connection'].get('can_configure')):
            raise chat_error('provider_unavailable')
        if state['connection']['stopped'] and state['connection'].get('can_configure'):
            self._resume_known()
        if self._locked or self._usage_status()['stopped'] or state['connection']['calls'] >= 48:
            raise chat_error('provider_unavailable')
        if persist and self._key_store is not None:
            try:
                self._key_store.save(key)
            except Exception:
                raise chat_error('state_unavailable') from None
        self._key = key
        self._armed = self._verified = False
        self._phase = 'configured_unverified'
        self._revision += 1
        self._requests.clear()

    def _clear(self):
        self._retire('cleared')
        self._session.interrupt_current(self._session.control_state().run)
        self._revision += 1
        self._requests.clear()

    def _disconnect(self):
        """Retire a persistent page's work; only explicit clear deletes storage."""
        if not self._session.control_state().persistent or self._key is None:
            raise chat_error('invalid_request')
        self._retire('failed')
        self._session.interrupt_current(self._session.control_state().run)
        self._revision += 1

    def action(self, path, body):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            if path == '/conversation' and type(body) is dict and body.get('action') in ('read_archive', 'read_history', 'summary_candidate'):
                target = 'archived_id' if body['action'] == 'read_archive' else 'run_id'
                if set(body) != {'action', target, 'conversation_id', 'revision'}:
                    raise chat_error('invalid_request')
                # A history read cannot retire the current transport or sample
                # a new execution. Keep the outer and durable revisions distinct.
                self._bound(body, {'conversation_id': self._session.control_state().conversation_id})
                result = self._session.action(path, {**body, 'revision': self._session.control_state().revision})
                result['revision'] = self._revision
                return result
            if path == '/preview':
                # Do not call _project/status: those retire expired runs and can
                # alter the connection. The estimate reads no credential bytes.
                if type(body) is not dict or not {'conversation_id', 'revision'} <= set(body):
                    raise chat_error('invalid_request')
                self._bound(body, {'conversation_id': self._session.control_state().conversation_id})
                result = self._session.action('/preview', {**body, 'revision': self._session.control_state().revision})
                result['revision'] = self._revision
                # The inner durable acceptance can consume a request before a
                # worker-start failure propagates to this outer controller.
                result['requests']['used'] = max(result['requests']['used'], self._requests_total)
                if (not self._armed or self._phase != 'ready' or self._locked):
                    if 'provider_unavailable' not in result['blockers']:
                        result['blockers'].append('provider_unavailable')
                # Sample physical worker state without cleaning up or retiring.
                for transport in self._transports:
                    try:
                        busy = transport.status()['busy'] is not False
                    except Exception:
                        busy = True
                    if busy and 'busy' not in result['blockers']:
                        result['blockers'].append('busy')
                if self._requests_total >= 24 and 'request_limit' not in result['blockers']:
                    result['blockers'].append('request_limit')
                return result
            fields = {
                '/send': {'request_id', 'message'}, '/stop': {'request_id'},
                '/reset': {'provider_id'}, '/connection': {'action'},
                '/material': {'action'},
                '/project': {'action'},
                '/followup': {'action'},
                '/web': {'action'},
                '/conversation': {'action'},
            }
            if type(path) is not str or path not in fields or type(body) is not dict:
                raise chat_error('invalid_request')
            extra = {'key'} if path == '/connection' and body.get('action') == 'configure' else set()
            if path == '/send':
                extra = {'material_sha256', 'project_sha256', 'followup_sha256', 'task_intent', 'creation_path', 'web_search', 'artifact_reference_sha256', 'history_run_ids', 'context_plan_sha256'} & set(body)
            if path == '/material' and body.get('action') == 'attach':
                extra = {'name', 'data'}
            if path == '/project' and body.get('action') == 'attach':
                extra = {'name', 'files'}
            if path == '/followup' and body.get('action') == 'select':
                extra = {'run_id'} | ({'round_count'} if 'round_count' in body else set())
            if path == '/web':
                extra = {'fetch': {'fetch_id', 'urls'}, 'cancel': {'fetch_id'},
                         'attach': {'fetch_id', 'project_sha256'}, 'clear': set()}.get(body.get('action'), set()) if type(body.get('action')) is str else set()
            if path == '/conversation':
                extra = {'select': {'selected'}, 'show': {'run_id'}, 'rename': {'title'}, 'delete': {'confirmed'}, 'refresh': set(),
                         'confirm': {'project_sha256', 'confirmed'},
                         'save_input': {'saved_input_id', 'message', 'task_intent', 'creation_path'},
                         'restore_input': {'saved_input_id'}, 'delete_input': {'saved_input_id'},
                         'prepare_continuation': set(), 'archive': {'confirmed'},
                         'select_history': {'run_ids'},
                         'adopt_summary': {'binding', 'text'}, 'withdraw_summary': {'summary_id'},
                         'restore_archive': {'archived_id'}}.get(
                             body.get('action'), set()) if type(body.get('action')) is str else set()
            if set(body) != {'conversation_id', 'revision'} | fields[path] | extra:
                raise chat_error('invalid_request')
            state = self._project()
            self._bound(body, state)
            if path in ('/material', '/project', '/followup', '/web', '/conversation'):
                if path == '/conversation' and body.get('action') in ('archive', 'restore_archive'):
                    if state['busy'] or self._transports:
                        raise chat_error('busy')
                    if (not state['connection']['cost_known']
                            or state['connection']['reserved_nano_usd'] != 0):
                        raise chat_error('usage_unavailable')
                if state['busy'] and not (path == '/conversation'
                        and body.get('action') in ('save_input', 'delete_input')):
                    raise chat_error('busy')
                self._session.action(path, {**body, 'revision': self._session.control_state().revision})
                if not (path == '/conversation' and body.get('action') in ('save_input', 'delete_input')):
                    self._revision += 1
                return self._project()
            if path == '/connection':
                action = body['action']
                if type(action) is not str or action not in ('configure', 'activate', 'clear', 'disconnect'):
                    raise chat_error('invalid_request')
                if action == 'configure':
                    self._configure(body['key'], state)
                elif action == 'disconnect':
                    self._disconnect()
                elif action == 'clear':
                    if self._key_store is not None:
                        try:
                            self._key_store.clear()
                        except Exception:
                            self._clear()
                            self._phase = 'failed'  # Keep explicit deletion retry available.
                            raise chat_error('state_unavailable') from None
                    self._clear()
                else:
                    if state['busy']:
                        raise chat_error('busy')
                    if state['connection'].get('can_reactivate'):
                        self._resume_known()
                        self._phase = 'configured_unverified'
                    if self._phase != 'configured_unverified' or self._key is None or self._locked:
                        raise chat_error('provider_unavailable')
                    self._activation_operation = self._session.control_state().run
                    self._armed, self._phase = True, 'ready'
                    self._revision += 1
                return self._project()
            if path == '/reset':
                if type(body['provider_id']) is not str or body['provider_id'] != 'offline':
                    raise chat_error('invalid_request')
                if self._session.check_new_conversation():
                    if not state['busy']:
                        self._session.action(path, {**body, 'revision': self._session.control_state().revision})
                        self._revision += 1
                        return self._project()
                self._clear()
                inner = {**body, 'revision': self._session.control_state().revision}
                self._session.action(path, inner)
                return self._project()
            if (type(body['request_id']) is not str
                    or re.fullmatch(r'[a-f0-9]{32}', body['request_id']) is None):
                raise chat_error('invalid_request')
            inner = {**body, 'revision': self._session.control_state().revision}
            if path == '/send':
                if body['request_id'] in self._requests:
                    if self._requests[body['request_id']] != body:
                        raise chat_error('request_conflict')
                    return self._project()
                if state['busy']:
                    raise chat_error('busy')
                if self._requests_total >= 24:
                    raise chat_error('request_limit')
                if state['connection']['calls'] >= 48:
                    raise chat_error('completion_request_limit')
                if not state['connection']['can_send']:
                    raise chat_error('provider_unavailable')
                if (body.get('web_search') is True and self._make_search is not None
                        and self._session.control_state().capacity_bound):
                    # Recheck the same read-only upper bound immediately before
                    # acceptance, leaving room for the first model dispatch.
                    preview = self._session.action('/preview', {key: value for key, value in inner.items()
                        if key in ('conversation_id', 'revision', 'message', 'task_intent',
                                   'project_sha256', 'creation_path', 'web_search', 'artifact_reference_sha256', 'history_run_ids', 'context_plan_sha256')})
                    for reason in ('budget_limit', 'usage_unavailable', 'completion_request_limit', 'token_limit'):
                        if reason in preview['blockers']:
                            raise chat_error(reason)
            try:
                self._session.action(path, inner)
            except Exception:
                # A worker-start failure can happen before a decider is assigned.
                self._retired(self._session.control_state().run)
                raise
            if path == '/send':
                self._requests[body['request_id']] = copy.deepcopy(body)
                self._requests_total += 1
            elif path == '/stop':
                # A bound stop also retires a configuration when its task has
                # just completed; the committed report and charges stay intact.
                self._retire('failed')
            return self._project()

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._retire('cleared')
                self._session.close()
