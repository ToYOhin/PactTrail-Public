"""Explicit, reversible derived history text; never execution permission."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from pacttrail.domain.runs import PactTrailError


MAX_RECORDS = 16
MAX_TEXT_BYTES = 2048
BINDING_FIELDS = {'version', 'conversation_id', 'run_id', 'request_revision', 'turn_sha256',
                  'source_sha256', 'artifact_sha256', 'artifact_kind', 'context_sha256'}
RECORD_FIELDS = BINDING_FIELDS | {'id', 'summary_version', 'text', 'text_sha256'}


def _require(value, code='history_corrupt'):
    if not value:
        raise PactTrailError('chat_error: ' + code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def _hash(value, size=64):
    _require(type(value) is str and re.fullmatch('[a-f0-9]{' + str(size) + '}', value) is not None)


def validate_binding(value):
    _require(type(value) is dict and set(value) == BINDING_FIELDS)
    _require(type(value['version']) is int and value['version'] == 1)
    for field in ('conversation_id', 'run_id'):
        _hash(value[field], 32)
    for field in ('turn_sha256', 'context_sha256'):
        _hash(value[field])
    for field in ('source_sha256', 'artifact_sha256'):
        if value[field] is not None:
            _hash(value[field])
    _require(type(value['request_revision']) is int and 0 <= value['request_revision'] < 2 ** 31
             and (value['artifact_kind'] is None and value['artifact_sha256'] is None
                  or value['artifact_kind'] in ('report', 'change_draft') and value['artifact_sha256'] is not None))
    return copy.deepcopy(value)


def text_digest(text):
    _require(type(text) is str and text.strip() and len(text) <= MAX_TEXT_BYTES
             and not any(ord(c) < 32 and c not in '\r\n\t' or ord(c) == 127
                         or 0xD800 <= ord(c) <= 0xDFFF for c in text))
    raw = text.encode('utf-8')
    _require(len(raw) <= MAX_TEXT_BYTES)
    return hashlib.sha256(raw).hexdigest()


def validate_record(value):
    _require(type(value) is dict and set(value) == RECORD_FIELDS)
    validate_binding({key: value[key] for key in BINDING_FIELDS})
    _hash(value['id'], 32)
    _require(type(value['summary_version']) is int and 1 <= value['summary_version'] <= MAX_RECORDS)
    _require(value['text_sha256'] == text_digest(value['text']))
    return copy.deepcopy(value)


def selected_turns(conversation):
    selection = conversation.get('history_selection')
    wanted = set(selection['run_ids']) if selection is not None else None
    return [turn for turn in conversation['turns']
            if turn['status'] == 'complete' and (wanted is None or turn['run_id'] in wanted)]


def context_fingerprint(conversation):
    from pacttrail.domain.artifact_reference import reference_identity
    project = conversation['project']
    reference = conversation.get('artifact_reference')
    return digest(dict(conversation_id=conversation['id'],
        turns=[dict(run_id=turn['run_id'], sha256=digest(turn)) for turn in selected_turns(conversation)],
        source_sha256=project['sha256'] if project else None,
        artifact_reference=reference_identity(reference) if reference else None))


def binding_for(conversation, turn):
    return dict(version=1, conversation_id=conversation['id'], run_id=turn['run_id'],
        request_revision=turn['request_revision'], turn_sha256=digest(turn),
        source_sha256=turn['source']['sha256'] if turn['source'] else None,
        artifact_sha256=turn['artifact']['sha256'] if turn['artifact'] else None,
        artifact_kind=('change_draft' if turn['intent'] == 'change_draft' else 'report') if turn['artifact'] else None,
        context_sha256=context_fingerprint(conversation))


def validate_ledger(value, conversation):
    _require(type(value) is dict and set(value) == {'version', 'records', 'active_ids'}
             and type(value['version']) is int and value['version'] == 1
             and type(value['records']) is list and len(value['records']) <= MAX_RECORDS
             and type(value['active_ids']) is list)
    records, versions = {}, {}
    available = {turn['run_id']: turn for turn in conversation['turns'] if turn['status'] == 'complete'}
    for record in value['records']:
        validate_record(record)
        _require(record['id'] not in records and record['conversation_id'] == conversation['id']
                 and record['run_id'] in available
                 and record['request_revision'] == available[record['run_id']]['request_revision']
                 and record['summary_version'] == versions.get(record['run_id'], 0) + 1)
        records[record['id']] = record
        versions[record['run_id']] = record['summary_version']
    _require(all(type(identifier) is str and identifier in records for identifier in value['active_ids'])
             and len(set(value['active_ids'])) == len(value['active_ids']))
    runs = [records[identifier]['run_id'] for identifier in value['active_ids']]
    _require(len(set(runs)) == len(runs))
    return copy.deepcopy(value)


def adopted_records(conversation, *, require_current=False):
    ledger = conversation.get('context_summaries')
    if ledger is None:
        return []
    records = {record['id']: record for record in ledger['records']}
    chosen = [copy.deepcopy(records[identifier]) for identifier in ledger['active_ids']]
    order = {turn['run_id']: index for index, turn in enumerate(conversation['turns'])}
    chosen.sort(key=lambda record: order[record['run_id']])
    available = {turn['run_id']: turn for turn in selected_turns(conversation)}
    if require_current:
        for record in chosen:
            turn = available.get(record['run_id'])
            _require(turn is not None and {key: record[key] for key in BINDING_FIELDS}
                     == binding_for(conversation, turn), 'stale_request')
    return chosen


def summary_plan(conversation):
    adopted = adopted_records(conversation)
    selected = {turn['run_id']: turn for turn in selected_turns(conversation)}
    active_ids = {record['id'] for record in adopted}
    items = []
    for record in conversation.get('context_summaries', {}).get('records', []):
        turn = selected.get(record['run_id'])
        current = bool(turn is not None and {key: record[key] for key in BINDING_FIELDS}
                       == binding_for(conversation, turn))
        items.append(dict(**copy.deepcopy(record), adopted=record['id'] in active_ids,
                          state='current' if current else 'stale'))
    state = 'none' if not adopted else 'stale' if any(
        item['adopted'] and item['state'] == 'stale' for item in items) else 'current'
    return dict(version=1, state=state, binding=summary_binding_for_records(adopted), items=items,
                max_records=MAX_RECORDS, max_text_bytes=MAX_TEXT_BYTES)


def summary_binding_for_records(records):
    _require(type(records) is list and len(records) <= MAX_RECORDS)
    for record in records:
        validate_record(record)
    _require(len({record['run_id'] for record in records}) == len(records))
    identities = [dict(id=record['id'], summary_version=record['summary_version'],
                      run_id=record['run_id'], turn_sha256=record['turn_sha256'],
                      text_sha256=record['text_sha256'], context_sha256=record['context_sha256'])
                  for record in sorted(records, key=lambda record: (record['run_id'], record['id']))]
    return digest(identities) if identities else None
