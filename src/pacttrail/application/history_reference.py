"""Bind an explicit history scope to the original send and read consumers."""
from __future__ import annotations

import copy
import re

from pacttrail.application.chat import chat_error
from pacttrail.domain.runs import PactTrailError
from pacttrail.domain.tool_conversation import validate_history_selection


class HistoryReferenceBindings:
    """Keep duplicate request scope separate from mutable conversation settings."""

    def __init__(self):
        self._requests = {}
        self._summary_requests = {}

    def restore_turn(self, turn, history_order):
        from pacttrail.domain.context_summary import summary_binding_for_records
        selected = turn.get('history_selection')
        wanted = set(selected['run_ids']) if selected else None
        self._requests[turn['run_id']] = ([run for run in history_order if run in wanted]
                                         if wanted is not None else None)
        self._summary_requests[turn['run_id']] = summary_binding_for_records(turn.get('derived_summaries', []))

    def check_send(self, conversation, body):
        has_summary = 'context_plan_sha256' in body
        summary = body.pop('context_plan_sha256', None)
        if has_summary and (type(summary) is not str or re.fullmatch('[a-f0-9]{64}', summary) is None):
            raise chat_error('invalid_request')
        present = 'history_run_ids' in body
        selection = body.pop('history_run_ids', None)
        if conversation is None:
            if present or has_summary:
                raise chat_error('invalid_request')
            return None
        request = body.get('request_id')
        # Validate the shape before comparing with the original request. A
        # duplicate may never silently acquire a different history envelope.
        if selection is not None:
            try:
                validate_history_selection(dict(version=1, run_ids=selection))
            except PactTrailError:
                raise chat_error('invalid_request') from None
        if (type(request) is str and request in self._requests
                and self._requests[request] != selection):
            raise chat_error('request_conflict')
        if type(request) is str and request in self._summary_requests:
            if self._summary_requests[request] != summary:
                raise chat_error('request_conflict')
        else:
            conversation.check_summary_binding(summary)
        conversation.check_history_binding(selection)
        return copy.deepcopy(selection)

    def accepted(self, request_id, selection, summary=None):
        self._requests[request_id] = copy.deepcopy(selection)
        self._summary_requests[request_id] = summary


def read_history(conversation, body, *, conversation_id, revision):
    """Return durable original text only, without a task/status projection."""
    return dict(version=1, mode='history_reference', conversation_id=conversation_id,
                revision=revision, turn=conversation.read_history_turn(body['run_id']))
