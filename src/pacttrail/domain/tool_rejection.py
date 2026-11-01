"""Closed, value-free reasons for tool response and consumer rejection."""
from __future__ import annotations

from types import MappingProxyType

from pacttrail.domain.runs import PactTrailError


_REASONS = MappingProxyType({
    'envelope': ('response', 'invalid_response'),
    'body_type': ('response', 'invalid_response'),
    'body_limit': ('response', 'response_limit'),
    'json_invalid': ('response', 'invalid_response'),
    'identity_mismatch': ('response', 'invalid_response'),
    'usage_invalid': ('response', 'usage_unavailable'),
    'usage_limit': ('response', 'token_limit'),
    'choices_shape': ('decision', 'invalid_response'),
    'choice_shape': ('decision', 'invalid_response'),
    'output_truncated': ('decision', 'response_limit'),
    'provider_interrupted': ('decision', 'provider_unavailable'),
    'finish_unsupported': ('decision', 'invalid_response'),
    'message_shape': ('decision', 'invalid_response'),
    'reasoning_not_supported': ('decision', 'invalid_response'),
    'summary_invalid': ('decision', 'invalid_response'),
    'tool_calls_shape': ('decision', 'invalid_response'),
    'tool_calls_type': ('decision', 'invalid_response'),
    'tool_calls_empty': ('decision', 'invalid_response'),
    'tool_calls_multiple': ('decision', 'invalid_response'),
    'tool_calls_limit': ('decision', 'invalid_response'),
    'tool_call_shape': ('decision', 'invalid_response'),
    'tool_call_object_type': ('decision', 'invalid_response'),
    'tool_call_fields': ('decision', 'invalid_response'),
    'tool_call_id_type': ('decision', 'invalid_response'),
    'tool_call_id_empty': ('decision', 'invalid_response'),
    'tool_call_id_characters': ('decision', 'invalid_response'),
    'tool_call_id_limit': ('decision', 'invalid_response'),
    'tool_call_type': ('decision', 'invalid_response'),
    'tool_call_function_type': ('decision', 'invalid_response'),
    'tool_call_function_fields': ('decision', 'invalid_response'),
    'tool_call_index': ('decision', 'invalid_response'),
    'tool_name': ('decision', 'invalid_response'),
    'arguments_encoding': ('decision', 'invalid_response'),
    'arguments_json': ('decision', 'invalid_response'),
    'arguments_schema': ('decision', 'invalid_response'),
    'duplicate_call_id': ('decision', 'invalid_response'),
    'decision_invalid': ('consumer', 'invalid_response'),
    'syntax_repair_unsupported': ('consumer', 'invalid_response'),
    'draft_repair_unsupported': ('consumer', 'invalid_response'),
    'tool_execution_failed': ('consumer', 'provider_unavailable'),
    'report_missing': ('consumer', 'invalid_response'),
    'report_invalid': ('consumer', 'invalid_response'),
})


class ToolResponseError(PactTrailError):
    """Keep the established public code; attach one catalogued reason only."""

    def __init__(self, reason):
        if type(reason) is not str or reason not in _REASONS:
            raise ValueError('invalid tool rejection reason')
        self.reason = reason
        super().__init__('model_evidence_error: ' + _REASONS[reason][1])


def tool_rejection(error):
    """Return a detached closed projection, never arbitrary exception content."""
    if type(error) is not ToolResponseError:
        return None
    reason = getattr(error, 'reason', None)
    if type(reason) is not str or reason not in _REASONS:
        return None
    stage, code = _REASONS[reason]
    if (type(error.args) is not tuple or len(error.args) != 1
            or type(error.args[0]) is not str
            or error.args[0] != 'model_evidence_error: ' + code):
        return None
    return {'stage': stage, 'reason': reason}
