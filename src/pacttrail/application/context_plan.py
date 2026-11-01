"""Text-free occupancy of the facts already owned by a send preview.

UTF-8 content bytes are distinct from tokens. Only the prepared, admitted request
supplies a token estimate; these rows never estimate tokens independently.
"""
from __future__ import annotations

import hashlib
import json

from pacttrail.domain.tool_conversation import CONTEXT_PREFIX, context_occupancy
from pacttrail.domain.context_summary import selected_turns, summary_plan
from pacttrail.platform.deepseek_tool_request import encode


def context_plan(message, conversation, source, *, message_limit, context_limit, search_requested):
    history = context_occupancy(conversation)
    history.update(limit_bytes=context_limit, excess_bytes=max(0, history['bytes'] - context_limit))
    for turn in history['items']:
        turn['reference'] = dict(kind='history', run_id=turn['run_id'])
    files = []
    if source is not None:
        for entry in source['files']:
            files.append(dict(file_id=entry['file_id'], path=entry['path'], sha256=entry['sha256'],
                manifest_bytes=len(encode(entry)), content_bytes=entry['byte_count'],
                reference=dict(kind='source', sha256=source['sha256'],
                               file_id=entry['file_id'], path=entry['path'])))
    result = dict(version=1,
        question=dict(bytes=len(message.encode('utf-8')), chars=len(message), limit_chars=message_limit,
                      excess_chars=max(0, len(message) - message_limit), reference=dict(kind='question')),
        history=history,
        sources=dict(sha256=source['sha256'] if source else None,
                     confirmed=source is not None and conversation['scope_state'] == 'confirmed',
                     manifest_bytes=len(encode(source)) if source else 0,
                     content_bytes=sum(entry['content_bytes'] for entry in files),
                     first_request='manifest_only' if source else 'none',
                     later='tool_read_only' if source else 'none', items=files),
        first_request=dict(bytes=None, other_bytes=None, input_limit_tokens=None, excess_input_tokens=None,
                           search='acquired_at_send' if search_requested else 'none'))
    if 'artifact_reference' in conversation:
        reference = conversation['artifact_reference']
        result['artifact_reference'] = dict(task_id=reference['task_id'], run_id=reference['run_id'],
            kind=reference['kind'], name=reference['artifact']['name'], sha256=reference['artifact']['sha256'],
            bytes=history['artifact_reference_bytes'], content_bytes=reference['artifact']['bytes'],
            confirmed=reference['state'] == 'confirmed',
            reference=dict(kind='artifact', task_id=reference['task_id'], run_id=reference['run_id'],
                           sha256=reference['artifact']['sha256']))
    if conversation.get('context_summaries', {}).get('records'):
        result['summary_plan'] = summary_plan(conversation)
    result['duplicates'] = duplicate_suggestions(conversation, source, history)
    return result


def duplicate_suggestions(conversation, source, history):
    """Exact equality hints only; never remove a reference or promise savings."""
    groups, suggestions = {}, []
    originals = {item['run_id'] for item in history['items'] if item.get('representation') != 'summary'}
    reference = conversation.get('artifact_reference')
    for turn in selected_turns(conversation):
        if turn['run_id'] not in originals:
            continue
        target = dict(kind='history', run_id=turn['run_id'])
        for kind, text in (('history_answer', turn['answer']),
                           ('history_artifact', turn['artifact']['text'] if turn['artifact'] else None)):
            if not text:
                continue
            raw = text.encode('utf-8')
            key = kind, hashlib.sha256(raw).hexdigest(), len(raw)
            groups.setdefault(key, []).append(target)
        if (reference is not None and turn['run_id'] == reference['run_id']
                and turn['artifact'] == reference['artifact']):
            suggestions.append(dict(kind='shared_artifact', sha256=reference['artifact']['sha256'],
                content_bytes=reference['artifact']['bytes'], wire_saving_bytes=0,
                recommendation='already_shared', references=[target,
                    dict(kind='artifact', task_id=reference['task_id'], run_id=reference['run_id'],
                         sha256=reference['artifact']['sha256'])]))
    if source is not None:
        for entry in source['files']:
            key = 'source_content', entry['sha256'], entry['byte_count']
            groups.setdefault(key, []).append(dict(kind='source', sha256=source['sha256'],
                file_id=entry['file_id'], path=entry['path']))
    for (kind, sha, size), refs in groups.items():
        if len(refs) > 1:
            suggestions.append(dict(kind=kind, sha256=sha, content_bytes=size,
                wire_saving_bytes=0 if kind == 'source_content' else None,
                recommendation='review_selected_files' if kind == 'source_content' else 'review_selected_history',
                references=refs))
    return suggestions[:16]


def prepared_occupancy(plan, request):
    """Account for the actual serialization, including escapes and tool schemas."""
    size = len(request)
    envelope = json.loads(request)
    history_wire = next((len(message['content'].encode('utf-8')) for message in envelope['messages']
        if message['role'] == 'user' and message.get('content', '').startswith(CONTEXT_PREFIX)), 0)
    logical = plan['history']['bytes']
    content = plan['question']['bytes'] + history_wire + plan['sources']['manifest_bytes']
    plan['first_request'].update(bytes=size, other_bytes=size - content,
        history_wire_bytes=history_wire, shared_bytes=max(0, logical - history_wire),
        sharing_overhead_bytes=max(0, history_wire - logical))
