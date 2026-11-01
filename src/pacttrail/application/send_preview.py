"""Read-only preparation of the main entry's next first model request.

The preview serializes in-memory conversation facts and a source manifest only.
It never creates a run, reads selected file bodies, or reserves admission costs.
"""
from __future__ import annotations

import copy
import re

from pacttrail.application.chat import chat_error
from pacttrail.application.context_plan import context_plan, prepared_occupancy
from pacttrail.application.ordinary_chat import valid_text
from pacttrail.domain.change_draft import validate_creation_path
from pacttrail.domain.conversation_capacity import OrdinaryCapacity
from pacttrail.domain.draft_failure import DRAFT_FAILURE_PREFIX, failure_draft
from pacttrail.domain.runs import PactTrailError
from pacttrail.domain.context_summary import summary_plan as project_summary_plan
from pacttrail.domain.tool_task import DecisionError, MAX_ANSWER_CHARS
from pacttrail.platform.deepseek_tool_protocol import prepare_initial_request


def preview_send(session, body):
    """Called under the consumer lock, before lifecycle/status side effects."""
    if session._closed:
        raise chat_error('closed')
    conversation = session._conversation
    if conversation is None or session._conversation_capacity is None:
        raise chat_error('invalid_request')
    required = {'conversation_id', 'revision', 'message', 'task_intent'}
    optional = {'project_sha256', 'creation_path', 'web_search', 'artifact_reference_sha256', 'history_run_ids', 'context_plan_sha256'}
    if (type(body) is not dict or not required <= set(body) or set(body) - required - optional
            or type(body['task_intent']) is not str
            or body['task_intent'] not in ('answer', 'report', 'change_draft')
            or not valid_text(body['message'], 4096)):
        raise chat_error('invalid_request')
    session._bound(body)
    conversation.check_history_binding(body.get('history_run_ids'))
    summary = project_summary_plan(conversation.restore())
    if ('context_plan_sha256' in body and (type(body['context_plan_sha256']) is not str
            or re.fullmatch('[a-f0-9]{64}', body['context_plan_sha256']) is None)):
        raise chat_error('invalid_request')
    if body.get('context_plan_sha256') != summary['binding']:
        raise chat_error('stale_request')
    reference = conversation.restore().get('artifact_reference')
    if body.get('artifact_reference_sha256') != (reference['artifact']['sha256'] if reference else None):
        raise chat_error('stale_request')
    if 'artifact_reference_sha256' in body and (type(body['artifact_reference_sha256']) is not str or reference is None):
        raise chat_error('invalid_request')
    snapshot = session._selected_project
    if body.get('project_sha256') != (snapshot['sha256'] if snapshot else None):
        raise chat_error('stale_request')
    if 'project_sha256' in body and (snapshot is None or type(body['project_sha256']) is not str):
        raise chat_error('invalid_request')
    intent = body['task_intent']
    search_requested = body.get('web_search', False)
    if (type(search_requested) is not bool or search_requested and
            (intent != 'answer' or session._web_search_factory is None)):
        raise chat_error('invalid_request')
    source = copy.deepcopy(snapshot)
    if source:
        for entry in source['files']:
            entry.pop('text', None)
    creation_path = body.get('creation_path')
    if 'creation_path' in body:
        if intent != 'change_draft' or source is None or session._project_reader_root is None:
            raise chat_error('invalid_request')
        try:
            creation_path = validate_creation_path(creation_path, source)
        except DecisionError:
            raise chat_error('invalid_request') from None
    if intent == 'change_draft' and (source is None or source['version'] != 1):
        raise chat_error('invalid_request')
    # Reuse the existing failure-followup creation binding without rereading files.
    if (intent == 'change_draft' and creation_path is None and summary['state'] != 'stale'
            and body['message'].startswith(DRAFT_FAILURE_PREFIX)):
        try:
            prior = failure_draft(body['message'], conversation.context(), source)
            creation_path = prior['changes'][0]['path'] if prior['version'] == 2 else None
            if creation_path is not None and session._project_reader_root is None:
                raise DecisionError()
        except PactTrailError as error:
            if str(error) != 'chat_error: context_limit':
                raise chat_error('stale_request') from None
        except Exception:
            raise chat_error('stale_request') from None
    chosen = conversation.restore()
    limits = session._conversation_capacity
    ordinary = session._ordinary_capacity or OrdinaryCapacity()
    message_limit = ordinary.message_chars if intent == 'answer' else 2048
    requests = getattr(session, '_process_requests', len(session._seen))
    plan = context_plan(body['message'], chosen, source, message_limit=message_limit,
                        context_limit=limits.max_context_bytes, search_requested=search_requested)
    result = dict(version=1, mode='send_preview', conversation_id=session._conversation_id,
        revision=session._revision, message_chars=len(body['message']), message_limit=message_limit,
        answer_chars=ordinary.answer_chars if intent == 'answer' else MAX_ANSWER_CHARS,
        attempts=dict(used=len(chosen['turns']), limit=limits.max_turns),
        context=dict(bytes=plan['history']['bytes'], limit=limits.max_context_bytes,
                     successful_turns=len(plan['history']['items'])),
        requests=dict(used=requests, limit=24),
        model=dict(calls_remaining=None, estimated_input_tokens=None, output_reserved_tokens=None,
                   reservation_nano_usd=None, budget_remaining_nano_usd=None, cost_known=None,
                   token_basis=None, pricing_basis=None),
        sources=dict(history_turns=len(plan['history']['items']),
                     files=[entry['path'] for entry in source['files']] if source else [],
                     content='manifest_only' if source else 'none'),
        blockers=[], estimate_error=None, context_plan=plan)
    blockers = result['blockers']
    if search_requested:
        result['search'] = dict(provider=session._web_search_provider, max_calls=2,
            reservation_nano_usd=1_500_000 if session._web_search_paid else 0,
            pricing_basis='deepseek-search-per-success-0.00075-usd' if session._web_search_paid else 'no-api-fee',
            model_estimate_excludes_search=True)
    for condition, reason in (
            (conversation.failed, 'history_unavailable'),
            (session._record_failed, 'state_unavailable'),
            (session._busy(), 'busy'),
            (session._completion_retired, 'provider_unavailable'),
            (chosen['scope_state'] == 'review_required', 'stale_request'),
            (reference is not None and reference['state'] != 'confirmed', 'stale_request'),
            (summary['state'] == 'stale', 'stale_request'),
            (len(body['message']) > message_limit, 'message_limit'),
            (requests >= 24, 'request_limit'),
            (len(chosen['turns']) >= limits.max_turns, 'turn_limit'),
            (result['context']['bytes'] > limits.max_context_bytes, 'context_limit')):
        if condition:
            blockers.append(reason)
    # Keep inspecting budgets even if a different dimension already refuses send.
    usage = session._model_status() if session._model_status else None
    if usage is not None:
        result['model'].update(calls_remaining=max(0, 48 - usage['calls']),
            budget_remaining_nano_usd=max(0, usage['budget_nano_usd'] - usage['accounted_nano_usd']
                                         - usage['reserved_nano_usd'])
                if usage['cost_known'] else None,
            cost_known=usage['cost_known'], token_basis=usage['token_basis'], pricing_basis=usage['pricing_basis'])
        for condition, reason in (
                (usage['calls'] >= 48, 'completion_request_limit'),
                (not usage['cost_known'], 'usage_unavailable'),
                (usage['stopped'], usage['stop_reason'] or 'provider_unavailable')):
            if condition and reason not in blockers:
                blockers.append(reason)
    if 'context_limit' in blockers or conversation.failed or summary['state'] == 'stale':
        result['estimate_error'] = ('history_unavailable' if conversation.failed else
                                  'stale_request' if summary['state'] == 'stale' else 'context_limit')
        return result
    try:
        _, request = prepare_initial_request(body['message'], source=source,
            intent=None if intent == 'report' else intent, creation_path=creation_path,
            ordinary_capacity=session._ordinary_capacity if intent == 'answer' else None,
            conversation_capacity=limits, conversation=conversation.context())
        prepared_occupancy(plan, request)
        if session._preview_request is None:
            result['estimate_error'] = 'token_count_unavailable'
        else:
            estimate = session._preview_request(request)
            for field in ('input_limit_tokens', 'excess_input_tokens'):
                plan['first_request'][field] = estimate.get(field)
            for field in ('estimated_input_tokens', 'output_reserved_tokens', 'reservation_nano_usd'):
                result['model'][field] = estimate[field]
            result['estimate_error'] = estimate['estimate_error']
            if estimate['admission_error'] and estimate['admission_error'] not in blockers:
                blockers.append(estimate['admission_error'])
            if search_requested and usage is not None and usage['cost_known']:
                reservation = result['model']['reservation_nano_usd']
                if (reservation is not None and reservation + result['search']['reservation_nano_usd']
                        > result['model']['budget_remaining_nano_usd'] and 'budget_limit' not in blockers):
                    blockers.append('budget_limit')
    except Exception:
        result['estimate_error'] = 'token_count_unavailable'
    return result
