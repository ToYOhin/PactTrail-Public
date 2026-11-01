"""Run-owned tool protocol with injected transport and independent admission.

Composition owns credentials/HTTP selection. No retry or text-protocol widening.
"""
from __future__ import annotations

import copy
from datetime import datetime
import math
import re
import threading
import time

from pacttrail.application.model_evidence import _safe_error, model_evidence_error
from pacttrail.domain.tool_rejection import ToolResponseError, tool_rejection
from pacttrail.domain.tool_task import MAX_ANSWER_CHARS, DecisionError, validate_source_descriptor
from pacttrail.domain.conversation_capacity import ConversationCapacity, OrdinaryCapacity
from pacttrail.domain.change_draft import validate_creation_path
from pacttrail.domain.task_followup import context_text, validate_context
from pacttrail.domain.tool_conversation import (
    context_text as conversation_text, validate_context as validate_conversation,
)
from pacttrail.platform.deepseek_admission import DeepSeekToolAdmission
from pacttrail.platform.deepseek_tool_request import (
    MODEL, build_request, encode, normalize_response_message, parse_json, system_context, validate_call_message,
    observation_to_wire, validate_summary, web_search_context, conversation_wire_text,
)


_ENDPOINT = 'https://api.deepseek.com/chat/completions'
_HTTP_ERRORS = {400: 'invalid_request', 422: 'invalid_request',
                401: 'provider_auth', 403: 'provider_auth', 429: 'provider_rate_limit'}


def local_runtime_context():
    """Date-granularity server facts, without network or user-supplied values.

    The offset is explicit because a local timezone name such as CST is ambiguous.
    A completion retains its first prepared system message across the whole run.
    """
    now = datetime.now().astimezone()
    offset = now.strftime('%z')
    return {'date': now.date().isoformat(), 'utc_offset': offset[:3] + ':' + offset[3:5],
            'source': 'server_system_clock'}


def prepare_initial_request(goal, *, source=None, intent=None, creation_path=None,
                            ordinary_capacity=None, conversation_capacity=None,
                            conversation=None, followup=None, runtime_context=None, web_search=None):
    """The same closed first request for dispatch and read-only local preview.

    This only serializes already-owned facts. It allocates no run or transport,
    reads no source file and performs no admission reservation.
    """
    runtime = (local_runtime_context() if runtime_context is None else runtime_context) if intent == 'answer' else None
    messages = [{'role': 'system', 'content': system_context(
                    source, intent=intent, creation_path=creation_path,
                    ordinary_capacity=ordinary_capacity,
                    conversation_capacity=conversation_capacity, runtime_context=runtime)},
                {'role': 'user', 'content': goal}]
    if followup is not None:
        messages.append({'role': 'user', 'content': context_text(followup)})
    elif conversation is not None:
        messages.append({'role': 'user', 'content': conversation_text(conversation,
            max_bytes=conversation_capacity.max_context_bytes if conversation_capacity else 16384)})
    if web_search is not None:
        if intent != 'answer':
            raise model_evidence_error('invalid_request')
        messages.append({'role': 'user', 'content': web_search_context(web_search)})
    body = build_request(messages, source=source, intent=intent,
                         creation_path=creation_path, ordinary_capacity=ordinary_capacity,
                         conversation_capacity=conversation_capacity)
    return messages, body


class DeepSeekToolCompletion:
    def __init__(self, transport, *, admission: DeepSeekToolAdmission, run_id, deadline,
                 cancel, clock=time.monotonic, completion_timeout=5):
        try:
            close_transport = getattr(transport, 'close', None)
        except Exception:
            raise model_evidence_error('invalid_request') from None
        if (not callable(transport) or type(admission) is not DeepSeekToolAdmission
                or not callable(close_transport)
                or type(run_id) is not str or re.fullmatch(r'[a-f0-9]{32}', run_id) is None
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not isinstance(cancel, threading.Event) or not callable(clock)
                or type(completion_timeout) not in (int, float)
                or not math.isfinite(completion_timeout) or not 0 < completion_timeout <= 60):
            raise model_evidence_error('invalid_request')
        self._transport, self._admission = transport, admission
        self._close_transport = close_transport
        self._run_id, self._deadline, self._cancel = run_id, deadline, cancel
        self._clock, self._timeout = clock, completion_timeout
        self._state_lock = threading.RLock()
        self._call_lock = threading.Lock()
        self._closed = False
        self._calls = 0
        self._goal = None
        self._source = None
        self._followup = None
        self._conversation = None
        self._web_search = None
        self._intent = None
        self._ordinary_capacity = None
        self._conversation_capacity = None
        self._creation_path = None
        self._messages = []
        self._observations = []
        self._pending = None
        self._saved_receipt = None
        self.saved_acknowledgement = None
        self._call_ids = set()
        self._started = self._now()
        if deadline - self._started > 60:
            raise model_evidence_error('invalid_request')

    @property
    def calls(self):
        with self._state_lock:
            return self._calls

    @property
    def completion_timeout(self):
        return self._timeout

    def set_source(self, source):
        """Bind one validated identity before dispatch; never retain caller ownership."""
        if not self._call_lock.acquire(blocking=False):
            raise model_evidence_error('invalid_request')
        try:
            with self._state_lock:
                if self._closed or self._source is not None or self._goal is not None or self._calls:
                    raise model_evidence_error('invalid_request')
                try:
                    self._source = validate_source_descriptor(source)
                except DecisionError:
                    raise model_evidence_error('invalid_request') from None
        finally:
            self._call_lock.release()

    def set_followup(self, context):
        """Own one historical reference under the already bound current source."""
        if not self._call_lock.acquire(blocking=False):
            raise model_evidence_error('invalid_request')
        try:
            with self._state_lock:
                if (self._closed or self._source is None or self._followup is not None
                        or self._goal is not None or self._calls or self._intent is not None
                        or self._conversation is not None or self._conversation_capacity is not None):
                    raise model_evidence_error('invalid_request')
                try:
                    chosen = validate_context(context)
                except Exception as error:
                    code = 'input_limit' if str(error) == 'chat_error: context_limit' else 'invalid_request'
                    raise model_evidence_error(code) from None
                if chosen['source'] != self._source:
                    raise model_evidence_error('invalid_request')
                self._followup = chosen
        finally:
            self._call_lock.release()

    def set_conversation(self, context):
        """Bind complete historical facts without granting their old scope or actions."""
        if not self._call_lock.acquire(blocking=False):
            raise model_evidence_error('invalid_request')
        try:
            with self._state_lock:
                if (self._closed or self._conversation is not None or self._followup is not None
                        or self._goal is not None or self._calls):
                    raise model_evidence_error('invalid_request')
                try:
                    chosen = validate_conversation(context,
                        max_bytes=self._conversation_capacity.max_context_bytes
                        if self._conversation_capacity is not None else 16384)
                except Exception as error:
                    code = 'input_limit' if str(error) == 'chat_error: context_limit' else 'invalid_request'
                    raise model_evidence_error(code) from None
                if len(chosen['turns']) > (self._conversation_capacity.max_turns
                                           if self._conversation_capacity is not None else 12):
                    raise model_evidence_error('input_limit')
                if any(turn['run_id'] == self._run_id for turn in chosen['turns']):
                    raise model_evidence_error('invalid_request')
                self._conversation = chosen
        finally:
            self._call_lock.release()

    def set_conversation_capacity(self, capacity):
        """Bind one launch-owned full-history policy before the historical facts."""
        if not self._call_lock.acquire(blocking=False):
            raise model_evidence_error('invalid_request')
        try:
            with self._state_lock:
                if (self._closed or self._conversation_capacity is not None
                        or self._conversation is not None or self._followup is not None
                        or self._goal is not None or self._calls
                        or type(capacity) is not ConversationCapacity):
                    raise model_evidence_error('invalid_request')
                try:
                    self._conversation_capacity = ConversationCapacity(capacity.max_turns,
                                                                        capacity.max_context_bytes)
                except (DecisionError, AttributeError):
                    raise model_evidence_error('invalid_request') from None
        finally:
            self._call_lock.release()

    def set_web_search(self, value):
        """Freeze one validated acquisition before the first model dispatch."""
        if not self._call_lock.acquire(blocking=False):
            raise model_evidence_error('invalid_request')
        try:
            with self._state_lock:
                if (self._closed or self._web_search is not None or self._intent != 'answer'
                        or self._followup is not None or self._goal is not None or self._calls):
                    raise model_evidence_error('invalid_request')
                try:
                    from pacttrail.domain.web_search import validate_search
                    self._web_search = copy.deepcopy(validate_search(value))
                except Exception:
                    raise model_evidence_error('invalid_request') from None
        finally:
            self._call_lock.release()

    def set_intent(self, intent):
        """Bind direct-answer mode or a local-project draft before dispatch."""
        if not self._call_lock.acquire(blocking=False):
            raise model_evidence_error('invalid_request')
        try:
            with self._state_lock:
                if (self._closed or self._intent is not None or self._followup is not None
                        or self._goal is not None or self._calls
                        or type(intent) is not str or intent not in ('answer', 'change_draft')
                        or intent == 'change_draft' and (self._source is None
                            or self._source['kind'] != 'selected_project' or self._source['version'] != 1)):
                    raise model_evidence_error('invalid_request')
                self._intent = intent
        finally:
            self._call_lock.release()


    def set_ordinary_capacity(self, capacity):
        """Bind launch-owned ordinary limits after intent and historical scope."""
        if not self._call_lock.acquire(blocking=False):
            raise model_evidence_error('invalid_request')
        try:
            with self._state_lock:
                if (self._closed or self._ordinary_capacity is not None
                        or self._intent != 'answer' or self._conversation is None
                        or self._goal is not None or self._calls or type(capacity) is not OrdinaryCapacity):
                    raise model_evidence_error('invalid_request')
                try:
                    self._ordinary_capacity = OrdinaryCapacity(capacity.message_chars, capacity.answer_chars)
                except (DecisionError, AttributeError):
                    raise model_evidence_error('invalid_request') from None
        finally:
            self._call_lock.release()


    def set_creation(self, path):
        """Bind the user's new-file proposal path after source and draft intent."""
        if not self._call_lock.acquire(blocking=False):
            raise model_evidence_error('invalid_request')
        try:
            with self._state_lock:
                if (self._closed or self._source is None or self._intent != 'change_draft'
                        or self._creation_path is not None or self._followup is not None
                        or self._goal is not None or self._calls):
                    raise model_evidence_error('invalid_request')
                try:
                    self._creation_path = validate_creation_path(path, self._source)
                except DecisionError:
                    raise model_evidence_error('invalid_request') from None
        finally:
            self._call_lock.release()

    def _now(self):
        try:
            value = self._clock()
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('clock')
            return value
        except Exception:
            raise model_evidence_error('provider_unavailable') from None

    def _current(self):
        now = self._now()
        if now < self._started:
            raise model_evidence_error('provider_unavailable')
        with self._state_lock:
            if self._closed or self._cancel.is_set():
                raise model_evidence_error('provider_unavailable')
        if now >= self._deadline:
            raise model_evidence_error('timeout')
        return now

    def close(self):
        # Never hold the state lock across an external callback or transport call.
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._close_transport()
        except Exception:
            pass

    def _request(self, goal, observations):
        self._saved_receipt = None
        if type(goal) is not str or type(observations) is not tuple or len(observations) > 7:
            raise model_evidence_error('invalid_request')
        if self._goal is None:
            if observations:
                raise model_evidence_error('invalid_request')
            messages, body = prepare_initial_request(goal, source=self._source, intent=self._intent,
                creation_path=self._creation_path, ordinary_capacity=self._ordinary_capacity,
                conversation_capacity=self._conversation_capacity, conversation=self._conversation,
                followup=self._followup, web_search=self._web_search)
            self._goal, self._messages = goal, messages
            return body
        if goal != self._goal or self._pending is None:
            raise model_evidence_error('invalid_request')
        message, decisions, number = self._pending
        prefix_count = len(self._observations)
        if (len(observations) != prefix_count + len(decisions)
                or encode(list(observations[:prefix_count])) != encode(self._observations)):
            raise model_evidence_error('invalid_request')
        results = []
        for offset, (call, decision, actual) in enumerate(zip(
                message['tool_calls'], decisions, observations[prefix_count:])):
            wire = observation_to_wire(actual, self._run_id, number + offset, decision,
                source=self._source, intent=self._intent, creation_path=self._creation_path)
            results.append({'role': 'tool', 'tool_call_id': call['id'],
                            'content': encode(wire).decode('utf-8')})
        if (all(actual['result']['status'] == 'ok' for actual in observations[prefix_count:])
                and observations[-1]['tool'] in ('save_report', 'save_draft')):
            self._saved_receipt = copy.deepcopy(observations[-1])
        messages = [*self._messages, copy.deepcopy(message), *results]
        if self._conversation is not None:
            messages[2] = {'role': 'user', 'content': conversation_wire_text(self._conversation,
                max_bytes=self._conversation_capacity.max_context_bytes if self._conversation_capacity else 16384)}
        body = build_request(messages, source=self._source, intent=self._intent,
                             creation_path=self._creation_path, ordinary_capacity=self._ordinary_capacity,
                             conversation_capacity=self._conversation_capacity)
        self._messages = messages
        self._observations = copy.deepcopy(list(observations))
        self._pending = None
        return body

    def _saved_acknowledgement(self, error):
        """A local closing limit cannot undo an acknowledged saved artifact.

        This is an application receipt, never invented provider output. Unknown
        usage, provider failures, cancellation and unsuccessful saves still fail.
        """
        if str(error) not in ('model_evidence_error: token_limit', 'model_evidence_error: input_limit'):
            return None
        receipt = self._saved_receipt
        account = self._admission.status()
        if (receipt is None or account['stopped'] or not account['cost_known']
                or account['reserved_nano_usd'] != 0):
            return None
        self._current()  # A stopped/expired run cannot be completed by a late receipt.
        result = receipt['result']
        label = '草案' if receipt['tool'] == 'save_draft' else '报告'
        summary = (f"{label}已保存，可查看{'和审阅' if label == '草案' else ''}。"
                   '模型收尾因上下文容量未取得；以下为应用核对的保存收据：'
                   f"{result['name']} · SHA-256 {result['sha256']} · {result['bytes']} 字节。"
                   '未应用文件，未执行验证。')
        return validate_summary(summary, 'invalid_request', max_chars=512)

    def _response(self, response):
        if (type(response) is not tuple or len(response) != 2 or type(response[0]) is not int
                or not 100 <= response[0] <= 599):
            raise ToolResponseError('envelope')
        status, raw = response
        if status != 200:
            raise model_evidence_error(_HTTP_ERRORS.get(status, 'provider_unavailable'))
        if type(raw) is not bytes:
            raise ToolResponseError('body_type')
        if len(raw) > 65536:
            raise ToolResponseError('body_limit')
        try:
            value = parse_json(raw, 'invalid_response')
        except Exception:
            raise ToolResponseError('json_invalid') from None
        if (type(value) is not dict or value.get('object') != 'chat.completion'
                or value.get('model') != MODEL):
            raise ToolResponseError('identity_mismatch')
        # Identified response usage survives timeout/cancel/bad choices. Never
        # infer a refund from whether the application accepted an executable step.
        try:
            self._admission._settle(value.get('usage'))
        except Exception as error:
            safe = _safe_error(error, 'provider_unavailable')
            if safe.args == ('model_evidence_error: usage_unavailable',):
                raise ToolResponseError('usage_invalid') from None
            if safe.args == ('model_evidence_error: token_limit',):
                raise ToolResponseError('usage_limit') from None
            raise safe from None
        return value

    def _decision(self, value):
        choices = value.get('choices')
        if type(choices) is not list or len(choices) != 1:
            raise ToolResponseError('choices_shape')
        choice = choices[0]
        if (type(choice) is not dict or type(choice.get('index')) is not int or choice['index'] != 0
                or type(choice.get('finish_reason')) is not str or type(choice.get('message')) is not dict):
            raise ToolResponseError('choice_shape')
        finish, message = choice['finish_reason'], choice['message']
        if finish == 'length':
            raise ToolResponseError('output_truncated')
        if finish in ('aborted', 'insufficient_system_resource'):
            raise ToolResponseError('provider_interrupted')
        if finish not in ('stop', 'tool_calls'):
            raise ToolResponseError('finish_unsupported')
        message = normalize_response_message(message, finish=finish == 'stop')
        if finish == 'stop':
            maximum = (self._ordinary_capacity.answer_chars if self._ordinary_capacity is not None else
                       MAX_ANSWER_CHARS if self._conversation is not None else 512 if self._intent == 'answer' else 1024)
            return validate_summary(message['content'], max_chars=maximum), None
        pairs = validate_call_message(message, source=self._source, intent=self._intent,
                                      creation_path=self._creation_path)
        if any(call_id in self._call_ids for call_id, _ in pairs):
            raise ToolResponseError('duplicate_call_id')
        self._call_ids.update(call_id for call_id, _ in pairs)
        decisions = [decision for _, decision in pairs]
        decision = decisions[0] if len(decisions) == 1 else {'kind': 'tools', 'calls': decisions}
        return decision, copy.deepcopy(message)

    def __call__(self, goal, observations):
        if not self._call_lock.acquire(blocking=False):
            raise model_evidence_error('provider_unavailable')
        guard_acquired = dispatched = validated = False
        try:
            self._current()
            if self.calls >= 8:
                raise model_evidence_error('request_limit')
            self._admission._acquire()
            guard_acquired = True
            try:
                body = self._request(goal, observations)
                permit = self._admission._prepare(body)
            except Exception as error:
                acknowledgement = self._saved_acknowledgement(error)
                if acknowledgement is None:
                    raise
                self.saved_acknowledgement = acknowledgement['summary']
                self.close()
                return acknowledgement
            # Recheck after potentially slow/reentrant estimation. Reservation
            # linearizes dispatch with close; no external transport under this lock.
            with self._state_lock:
                start = self._current()
                timeout = min(self._timeout, self._deadline - start)
                self._admission._reserve(permit)
                self._calls += 1
                dispatched = True
            try:
                response = self._transport(_ENDPOINT, body, timeout)
            except TimeoutError:
                raise model_evidence_error('timeout') from None
            except Exception as error:
                raise _safe_error(error, 'provider_unavailable') from None
            value = self._response(response)
            now = self._current()
            if now - start < 0:
                raise model_evidence_error('provider_unavailable')
            if now - start >= timeout:
                raise model_evidence_error('timeout')
            decision, message = self._decision(value)
            self._current()
            if message is not None:
                decisions = decision['calls'] if decision['kind'] == 'tools' else [decision]
                self._pending = (message, copy.deepcopy(decisions), len(observations) + 1)
            validated = True
            from pacttrail.platform.synthetic_tool_transport import _consume_syntax_repair_refusal
            if _consume_syntax_repair_refusal(self._transport, body, value):
                from pacttrail.domain.draft_failure import DRAFT_FAILURE_PREFIX
                reason = 'draft_repair_unsupported' if goal.startswith(DRAFT_FAILURE_PREFIX) else 'syntax_repair_unsupported'
                raise ToolResponseError(reason) from None
            if decision['kind'] == 'finish':
                self.close()
            return decision
        except Exception as error:
            self.close()
            if tool_rejection(error) is not None:
                raise error from None
            raise _safe_error(error, 'provider_unavailable') from None
        finally:
            if guard_acquired:
                if dispatched and not validated:
                    self._admission._stop()
                self._admission._release()
            self._call_lock.release()
