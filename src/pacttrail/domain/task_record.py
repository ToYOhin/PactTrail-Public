"""Closed, bounded task facts; this format is not an executable checkpoint."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re

from pacttrail.domain.tool_task import MAX_ANSWER_CHARS, MAX_GOAL_CHARS, TASK_LIMITS, DecisionError, source_file_ids, validate_source_descriptor
from pacttrail.domain.runs import PactTrailError
from pacttrail.domain.tool_rejection import ToolResponseError, tool_rejection


MAX_RECORD_BYTES = 32768
MAX_RECORDS = 24
RECORD_CODES = frozenset({'record_corrupt', 'record_partial', 'record_version', 'record_unavailable',
                          'record_missing', 'record_read_only', 'record_conflict',
                          'record_report_unavailable', 'record_artifact_changed'})
FACT_ERRORS = RECORD_CODES | frozenset({
    'invalid_request', 'access_denied', 'busy', 'stale_request', 'request_conflict', 'turn_limit',
    'request_limit', 'context_limit', 'response_limit', 'state_unavailable', 'timeout',
    'source_unavailable', 'invalid_response', 'closed', 'provider_auth', 'provider_rate_limit',
    'provider_unavailable', 'token_count_unavailable', 'token_limit', 'budget_limit',
    'usage_unavailable', 'completion_request_limit', 'interrupted', 'invalid_argument',
    'read_limit', 'output_unavailable', 'artifact_conflict', 'tool_error',
})
_TOOLS = ('search', 'read', 'save_report')
_USAGE = ('prompt_tokens', 'completion_tokens', 'total_tokens',
          'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
_MAX_INTEGER = 2 ** 53 - 1
SEARCH_NANO_USD = 750_000
SEARCH_PRICING_BASIS = 'deepseek-search-per-success-0.00075-usd'
COST_LEDGER_FIELDS = ('ledger_version', 'search_calls', 'model_accounted_nano_usd',
                      'search_accounted_nano_usd', 'model_reserved_nano_usd',
                      'search_reserved_nano_usd', 'search_pricing_basis')


class TaskRecordError(ValueError):
    """Expose only a fixed code, never paths, contents or underlying exceptions."""

    def __init__(self, code='record_corrupt'):
        self.code = code if code in RECORD_CODES else 'record_unavailable'
        super().__init__('task_record_error: ' + self.code)


def validate_draft_rejection(value, *, status, error, pending):
    """One confirmed closed diagnosis, never inferred from an error code."""
    if value is None:
        return None
    try:
        _object(value, ('stage', 'reason'))
        _require(status == 'error' and type(error) is str and pending is None)
        confirmed = ToolResponseError(value['reason'])
        _require(tool_rejection(confirmed) == value
                 and confirmed.args == ('model_evidence_error: ' + error,))
        return copy.deepcopy(value)
    except TaskRecordError:
        raise
    except (KeyError, TypeError, ValueError):
        raise TaskRecordError('record_corrupt') from None


def _require(condition):
    if not condition:
        raise TaskRecordError('record_corrupt')


def _object(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _integer(value, minimum=0, maximum=_MAX_INTEGER):
    _require(type(value) is int and minimum <= value <= maximum)


def _text(value, limit, *, empty=False, byte_limit=None):
    _require(type(value) is str and (empty or bool(value.strip())) and len(value) <= limit)
    _require(not any((ord(c) < 32 and c not in '\r\n\t') or ord(c) == 127
                     or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    if byte_limit is not None:
        _require(len(value.encode('utf-8')) <= byte_limit)


def identifier(value):
    _require(type(value) is str and re.fullmatch('[a-f0-9]{32}', value) is not None)
    return value


def _hash(value):
    _require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None)


def _error(value):
    _require(value is None or type(value) is str and value in FACT_ERRORS)


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def record_digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _receipt(value, run_id, *, draft=False):
    _object(value, ('name', 'sha256', 'bytes'))
    _require(value['name'] == ('draft-' + run_id + '.json' if draft else 'report-' + run_id + '.md'))
    _hash(value['sha256'])
    _integer(value['bytes'], 1, 8192)


def preview_task_steps(steps):
    """Detach public observations; never serialize model messages or tool arguments."""
    _require(type(steps) is list and len(steps) <= 8)
    result = copy.deepcopy(steps)
    for step in result:
        _require(type(step) is dict and type(step.get('result')) is dict)
        observation = step['result']
        if observation.get('status') != 'ok':
            continue
        if step.get('tool') == 'read':
            _require(type(observation.get('text')) is str)
            observation['text'] = observation['text'].encode('utf-8')[:512].decode('utf-8', errors='ignore')
            observation['preview'] = True
        elif step.get('tool') == 'search':
            _require(type(observation.get('matches')) is list)
            observation['matches'] = [
                {**match, 'text': match['text'].encode('utf-8')[:128].decode('utf-8', errors='ignore')}
                for match in observation['matches'][:4]]
            observation['preview'] = True
    return result


def _steps(steps, source, run_id, *, draft=False, answer=False):
    _require(type(steps) is list and len(steps) <= 8)
    files = source_file_ids(source)
    entries = ({entry['file_id']: entry for entry in source['files']}
               if source and source['kind'] == 'selected_project' else
               {'selected.txt': source} if source else {})
    for number, step in enumerate(steps, 1):
        _object(step, ('index', 'kind', 'tool', 'reason', 'result'))
        _integer(step['index'], number, number)
        _require(step['kind'] in ('tool', 'finish'))
        _text(step['reason'], (4096 if answer else MAX_ANSWER_CHARS) if step['kind'] == 'finish' else 256)
        result = step['result']
        _require(type(result) is dict)
        if step['kind'] == 'finish':
            _require(step['tool'] is None and result == {'status': 'ok'} and number == len(steps))
            continue
        tool = step['tool']
        _require(type(tool) is str and tool in (('search', 'read', 'save_draft') if draft else _TOOLS))
        if result.get('status') == 'error':
            _object(result, ('status', 'error'))
            _require(type(result['error']) is str and result['error'] in FACT_ERRORS)
            continue
        _require(result.get('status') == 'ok')
        if tool in ('save_report', 'save_draft'):
            _object(result, {'status', 'name', 'sha256', 'bytes'} | ({'read_bytes'} if draft else set()))
            _receipt({key: result[key] for key in ('name', 'sha256', 'bytes')}, run_id, draft=draft)
            if draft:
                _integer(result['read_bytes'], 1, source['byte_count'])
        elif tool == 'read':
            _object(result, ('status', 'file_id', 'text', 'sha256', 'read_bytes', 'preview'))
            _require(type(result['file_id']) is str and result['file_id'] in files and result['preview'] is True)
            _text(result['text'], 512, empty=True, byte_limit=512)
            _hash(result['sha256'])
            _integer(result['read_bytes'], 1, 8192)
            entry = entries.get(result['file_id'])
            if entry:
                _require(result['sha256'] == entry['sha256'] and result['read_bytes'] == entry['byte_count'])
        else:
            _object(result, ('status', 'matches', 'read_bytes', 'preview'))
            _require(result['preview'] is True and type(result['matches']) is list and len(result['matches']) <= 4)
            _integer(result['read_bytes'], 0, TASK_LIMITS['max_read_bytes'])
            if source:
                _require(result['read_bytes'] == source['byte_count'])
            for match in result['matches']:
                _object(match, ('file_id', 'line', 'text'))
                _require(type(match['file_id']) is str and match['file_id'] in files)
                _integer(match['line'], 1, entries.get(match['file_id'], {}).get('line_count', 8192))
                _text(match['text'], 128, empty=True, byte_limit=128)


def _budget(value):
    fields = {'decisions': 8, 'tools': 7, 'read_bytes': 32768, 'total_decisions': 48,
              'total_tools': 42, 'total_read_bytes': 196608, 'model_dispatches': 48,
              'model_cost_nano': _MAX_INTEGER}
    _object(value, (*TASK_LIMITS, *fields, 'deadline_seconds'))
    for key, expected in TASK_LIMITS.items():
        _integer(value[key], expected, expected)
    for key, maximum in fields.items():
        _integer(value[key], 0, maximum)
    for key in ('decisions', 'tools', 'read_bytes'):
        _require(value[key] <= value['total_' + key])
    timeout = value['deadline_seconds']
    _require(type(timeout) in (float, int) and math.isfinite(timeout) and 0 < timeout <= 3600)


def _cost(value):
    ledger = type(value) is dict and 'ledger_version' in value
    _object(value, ('scope', 'mode', 'calls', 'reserved_nano_usd', 'accounted_nano_usd',
                    'budget_nano_usd', 'cost_known', 'stopped', 'stop_reason', 'token_basis',
                    'pricing_basis', 'input_estimate_tokens', 'usage',
                    *(COST_LEDGER_FIELDS if ledger else ())))
    _require(value['scope'] == 'process' and value['mode'] in ('synthetic', 'synthetic_protocol', 'model_protocol'))
    _integer(value['calls'], 0, 48)
    for field in ('reserved_nano_usd', 'accounted_nano_usd', 'budget_nano_usd'):
        _integer(value[field])
    _require(type(value['cost_known']) is bool and type(value['stopped']) is bool)
    _error(value['stop_reason'])
    _require(value['token_basis'] in ('synthetic-no-model', 'synthetic-test-only', 'local-estimate'))
    _require(value['pricing_basis'] in ('synthetic-no-model', 'deepseek-peak-2026-09-11'))
    if ledger:
        _integer(value['ledger_version'], 1, 1)
        _integer(value['search_calls'], 0, 48)
        for field in ('model_accounted_nano_usd', 'search_accounted_nano_usd',
                      'model_reserved_nano_usd', 'search_reserved_nano_usd'):
            _integer(value[field])
        _require(value['mode'] != 'synthetic' and value['search_pricing_basis'] == SEARCH_PRICING_BASIS)
        for total, model, search in (
                ('accounted_nano_usd', 'model_accounted_nano_usd', 'search_accounted_nano_usd'),
                ('reserved_nano_usd', 'model_reserved_nano_usd', 'search_reserved_nano_usd')):
            _require(value[total] == value[model] + value[search])
        _require(value['search_accounted_nano_usd'] % SEARCH_NANO_USD == 0
                 and value['search_accounted_nano_usd'] <= value['search_calls'] * SEARCH_NANO_USD
                 and value['search_reserved_nano_usd'] in (0, SEARCH_NANO_USD))
        _require(not value['cost_known'] or value['reserved_nano_usd'] == 0)
        if value['search_reserved_nano_usd']:
            _require(value['search_calls'] > 0 and value['model_reserved_nano_usd'] == 0
                     and value['search_accounted_nano_usd'] <= (value['search_calls'] - 1) * SEARCH_NANO_USD)
    if value['mode'] == 'synthetic':
        _require(all(value[key] == 0 for key in ('calls', 'reserved_nano_usd', 'accounted_nano_usd', 'budget_nano_usd'))
                 and value['token_basis'] == value['pricing_basis'] == 'synthetic-no-model')
    if value['input_estimate_tokens'] is not None:
        _integer(value['input_estimate_tokens'])
    if value['usage'] is not None:
        _object(value['usage'], _USAGE)
        for token in value['usage'].values():
            _integer(token)
        usage = value['usage']
        _require(usage['prompt_tokens'] + usage['completion_tokens'] == usage['total_tokens']
                 and usage['prompt_cache_hit_tokens'] + usage['prompt_cache_miss_tokens'] == usage['prompt_tokens'])


def validate_task_record(value):
    """Validate and own one bounded facts-only record; never normalize bad evidence."""
    try:
        _require(type(value) is dict)
        fields = {'version', 'run_id', 'revision', 'created_at', 'updated_at', 'goal',
                  'status', 'error', 'task', 'pending', 'cost'}
        if 'artifact_reference' in value:
            fields.add('artifact_reference')
        if value.get('version') in (2, 3):
            fields.add('parent')
        if value.get('version') == 3:
            fields.add('followup_context')
        _object(value, fields)
        _integer(value['version'], 1, 2 ** 31 - 1)
        if value['version'] not in (1, 2, 3, 4, 5, 6):
            raise TaskRecordError('record_version')
        identifier(value['run_id'])
        if value.get('artifact_reference') is not None:
            from pacttrail.domain.artifact_reference import validate_identity
            reference = validate_identity(value['artifact_reference'])
            _require(reference['run_id'] != value['run_id'])
        if value['version'] in (2, 3):
            from pacttrail.domain.task_followup import validate_parent_reference
            validate_parent_reference(value['parent'], child_run_id=value['run_id'])
        _integer(value['revision'], 1, 256)
        for key in ('created_at', 'updated_at'):
            _integer(value[key], 0, 253402300799)
        _require(value['created_at'] <= value['updated_at'])
        _text(value['goal'], 4096 if value['version'] == 5 else MAX_GOAL_CHARS)
        _require(type(value['status']) is str and value['status'] in ('running', 'complete', 'stopped', 'error'))
        _error(value['error'])
        task = value['task']
        draft = value['version'] in (4, 6)
        answer = value['version'] == 5
        _object(task, {'workspace', 'source', 'steps', 'artifact', 'budget'}
                | ({'intent'} if draft or answer else set()) | ({'rejection'} if value['version'] == 6 else set()))
        source = task['source']
        _require(value['version'] in (1, 5) or source is not None)
        if source is not None:
            validate_source_descriptor(source)
        if draft:
            _require(task['intent'] == 'change_draft' and source is not None
                     and source['kind'] == 'selected_project' and source['version'] == 1)
        if answer:
            _require(task['intent'] == 'answer')
        if value['version'] == 3:
            from pacttrail.domain.task_followup import context_rounds, validate_context
            context = validate_context(value['followup_context'])
            _require(context['version'] == 2 and context['source'] == source
                     and context['parent'] == value['parent'])
            _require(all(entry['parent']['run_id'] != value['run_id'] for entry in context_rounds(context)))
        workspace = 'selected-project' if source and source['kind'] == 'selected_project' else 'selected-text' if source else 'synthetic-project'
        _require(task['workspace'] == workspace)
        _steps(task['steps'], source, value['run_id'], draft=draft, answer=answer)
        _budget(task['budget'])
        _require(sum(step['kind'] == 'tool' for step in task['steps']) <= task['budget']['tools'])
        if task['artifact'] is not None:
            _receipt(task['artifact'], value['run_id'], draft=draft)
            _require(any(step['tool'] == ('save_draft' if draft else 'save_report')
                         and {key: item for key, item in step['result'].items() if key != 'read_bytes'}
                         == {'status': 'ok', **task['artifact']}
                         for step in task['steps']))
        pending = value['pending']
        if pending is not None:
            if type(pending) is dict and pending.get('kind') == 'search':
                _object(pending, ('kind', 'tool', 'index', 'request_id', 'reservation_nano_usd'))
                _require(pending['tool'] is None)
                _integer(pending['index'], 1, 48)
                identifier(pending['request_id'])
                _integer(pending['reservation_nano_usd'], SEARCH_NANO_USD, SEARCH_NANO_USD)
            else:
                _object(pending, ('kind', 'tool', 'index'))
                _integer(pending['index'], 1, 8)
                _require(pending['kind'] in ('model', 'tool') and (
                    pending['tool'] is None if pending['kind'] == 'model' else
                    pending['tool'] in (('search', 'read', 'save_draft') if draft else _TOOLS)))
        if value['status'] == 'complete':
            _require(value['error'] is None and pending is None and (answer or task['artifact'] is not None)
                     and task['steps'] and task['steps'][-1]['kind'] == 'finish')
        _cost(value['cost'])
        if value['cost'].get('ledger_version') == 1:
            _require(task['budget']['model_cost_nano'] == value['cost']['model_accounted_nano_usd'])
        if pending is not None and pending['kind'] == 'search':
            _require(value['cost'].get('ledger_version') == 1
                     and pending['index'] == value['cost']['search_calls']
                     and value['cost']['search_reserved_nano_usd'] == pending['reservation_nano_usd'])
        if value['version'] == 6:
            validate_draft_rejection(task['rejection'], status=value['status'], error=value['error'], pending=pending)
        _require(len(canonical_json(value)) <= MAX_RECORD_BYTES)
        return copy.deepcopy(value)
    except TaskRecordError:
        raise
    except (PactTrailError, DecisionError, KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise TaskRecordError('record_corrupt') from None
