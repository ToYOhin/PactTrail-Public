"""Offline wire fixture using actual observations; never constructs HTTP."""
from __future__ import annotations

import hashlib
import threading

from pacttrail.application.tool_task import SyntheticTaskDecision
from pacttrail.domain.syntax_repair import SyntaxRepairUnsupported
from pacttrail.domain.draft_failure import DraftRepairUnsupported
from pacttrail.platform.deepseek_protocol import _ENDPOINT, _MODEL, _encode
from pacttrail.platform.deepseek_tool_request import (
    conversation_from_request, creation_from_system, followup_from_request, intent_from_system,
    source_from_system, validate_request_with_observations, ordinary_capacity_from_system, conversation_capacity_from_system,
    web_search_from_request,
)


class SyntheticToolTransport:
    """Produce function-call envelopes from the observations in request bytes.

    The declared usage is synthetic; it only exercises the real fee consumer.
    No request text or exception is retained in public telemetry.
    """

    def __init__(self, estimate_tokens):
        self._estimate = estimate_tokens
        self._closed = False
        self._calls = 0
        self._syntax_repair_refusal = None
        self._lock = threading.Lock()

    def __call__(self, endpoint, body, timeout):
        with self._lock:
            if self._closed or endpoint != _ENDPOINT or not 0 < timeout <= 60:
                raise ValueError('transport unavailable')
            self._syntax_repair_refusal = None
            self._calls += 1
            call_id = 'call_' + str(self._calls)
        request, observations = validate_request_with_observations(body)
        messages = request['messages']
        decider = SyntheticTaskDecision()
        source = source_from_system(messages[0]['content'])
        if source is not None:
            decider.set_source(source)
        intent = intent_from_system(messages[0]['content'])
        if intent is not None:
            decider.set_intent(intent)
        creation_path = creation_from_system(messages[0]['content'])
        if creation_path is not None:
            decider.set_creation(creation_path)
        followup = followup_from_request(request)
        if followup is not None:
            decider.set_followup(followup)
        conversation = conversation_from_request(request)
        conversation_capacity = conversation_capacity_from_system(messages[0]['content'])
        if conversation_capacity is not None:
            decider.set_conversation_capacity(conversation_capacity)
        if conversation is not None:
            decider.set_conversation(conversation)
        search = web_search_from_request(request)
        if search is not None:
            decider.set_web_search(search)
        capacity = ordinary_capacity_from_system(messages[0]['content'])
        if capacity is not None:
            decider.set_ordinary_capacity(capacity)
        refused = False
        try:
            decision = decider(messages[1]['content'], observations)
        except (SyntaxRepairUnsupported, DraftRepairUnsupported) as error:
            refused = True
            summary = ('Synthetic fixture cannot repair an unapplied draft.' if type(error) is DraftRepairUnsupported
                       else 'Synthetic syntax repair does not support this failure line.')
            decision = {'kind': 'finish', 'summary': summary}
        if decision['kind'] == 'finish':
            message = {'role': 'assistant', 'content': decision['summary']}
            finish = 'stop'
        else:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': call_id, 'type': 'function', 'function': {
                    'name': decision['tool'],
                    'arguments': _encode({**decision['arguments'], 'reason': decision['reason']}).decode('utf-8'),
                },
            }]}
            finish = 'tool_calls'
        count = self._estimate(body).input_tokens
        output = min(256, request['max_tokens'])
        value = {
            'object': 'chat.completion', 'model': _MODEL,
            'choices': [{'index': 0, 'finish_reason': finish, 'message': message}],
            'usage': {'prompt_tokens': count, 'completion_tokens': output,
                      'total_tokens': count + output,
                      'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': count},
        }
        raw = _encode(value)
        if refused:
            with self._lock:
                if not self._closed:
                    self._syntax_repair_refusal = (hashlib.sha256(body).digest(), hashlib.sha256(raw).digest())
        return 200, raw

    def _consume_syntax_repair_refusal(self, body, value):
        with self._lock:
            receipt = self._syntax_repair_refusal
            self._syntax_repair_refusal = None
        return receipt is not None and receipt == (
            hashlib.sha256(body).digest(), hashlib.sha256(_encode(value)).digest())

    def close(self):
        with self._lock:
            self._closed = True
            self._syntax_repair_refusal = None


def _consume_syntax_repair_refusal(transport, body, value):
    """Only this actual synthetic adapter can identify its one settled refusal."""
    if not isinstance(transport, SyntheticToolTransport):
        return False
    return SyntheticToolTransport._consume_syntax_repair_refusal(transport, body, value)
