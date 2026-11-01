"""Verified artifact lineage and textual comparison, without execution authority."""
from __future__ import annotations

import difflib
import json

from pacttrail.application.chat import _bytes
from pacttrail.domain.task_record import TaskRecordError, record_digest
from pacttrail.platform.task_record_store import TaskRecordStore


MAX_DIFF_CHARS = 16000
MAX_DIFF_LINES = 256


def _side(workspace, task_id, run_id, *, expected=None):
    side = dict(task_id=task_id, run_id=run_id, kind=None, goal=None,
                state='unavailable', source_state=None, artifact=None)
    record = None
    try:
        if task_id not in workspace.history_tasks():
            side['state'] = 'missing'
            return side, None
        store = TaskRecordStore(workspace.history_task_dir(task_id))
        detail = store.read_record(run_id)
        record = detail['record']
        kind = 'change_draft' if record['version'] in (4, 6) else 'report'
        # A changed record cannot replace the identity that this new result
        # actually referenced. Keep that expected kind visible with no body.
        side.update(kind=expected['kind'] if expected is not None else kind, goal=record['goal'])
        if expected is not None and (record_digest(record) != expected['record_sha256']
                or kind != expected['kind'] or record['task']['artifact'] is None
                or record['task']['artifact']['sha256'] != expected['artifact_sha256']):
            side['state'] = 'changed'
            return side, record
        if detail['recovery'] == 'write_unknown':
            side['state'] = 'unknown'
            return side, record
        if record['task']['artifact'] is None:
            return side, record
        if detail['integrity'] not in ('verified', 'source_changed'):
            side['state'] = 'missing' if store.artifact_missing(run_id) else 'changed'
            return side, record
        artifact = store.report(run_id, historical_reference=True)
        final = store.read_record(run_id)
        if final['record'] != record or final['integrity'] not in ('verified', 'source_changed'):
            side['state'] = 'changed'
            return side, record
        side.update(state='verified', source_state=final['integrity'], artifact=artifact)
        return side, record
    except TaskRecordError as error:
        side['state'] = ('missing' if error.code in ('record_missing', 'record_report_unavailable')
                         else 'changed' if error.code == 'record_artifact_changed' else 'unavailable')
    except Exception:
        pass
    return side, record


def _contents(side):
    if side['kind'] == 'report':
        return {'报告正文': side['artifact']['text']}
    # TaskRecordStore already validates the closed draft and every new-text hash.
    draft = json.loads(side['artifact']['text'])
    return {item['path']: item['new_text'] for item in draft['changes']}


def _difference(previous, current):
    comparison = dict(state='unavailable', kind=None, files=[], truncated=False)
    if previous['state'] != 'verified' or current['state'] != 'verified':
        return comparison
    mixed = previous['kind'] != current['kind']
    if mixed:
        before = {'成果原文': previous['artifact']['text']}
        after = {'成果原文': current['artifact']['text']}
    else:
        before, after = _contents(previous), _contents(current)
    remaining_chars, remaining_lines = MAX_DIFF_CHARS, MAX_DIFF_LINES
    files = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path, ''), after.get(path, '')
        change = ('added' if path not in before else 'removed' if path not in after
                  else 'unchanged' if old == new else 'changed')
        pieces = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile='旧成果/' + path, tofile='新成果/' + path, lineterm='\n')
        kept = []
        for piece in pieces:
            if not piece.endswith('\n'):
                piece += '\n\\ No newline at end of file\n'
            count = len(piece.splitlines())
            if len(piece) > remaining_chars or count > remaining_lines:
                comparison['truncated'] = True
                break
            kept.append(piece)
            remaining_chars -= len(piece)
            remaining_lines -= count
        files.append(dict(path=path, change=change, diff=''.join(kept)))
    comparison.update(state='different_kind' if mixed else 'ready',
                      kind=None if mixed else current['kind'], files=files)
    return comparison


def artifact_versions(workspace, task_id, run_id, *, conversation_id, revision, legacy_reader=None):
    current, record = _side(workspace, task_id, run_id)
    relation = dict(state='unrecorded', reference=None)
    if record is not None:
        if 'artifact_reference' in record:
            reference = record['artifact_reference']
            relation = dict(state='none' if reference is None else 'recorded', reference=reference)
        elif callable(legacy_reader):
            try:
                relation = legacy_reader(record)
            except Exception:
                pass
    previous = None
    if relation['reference'] is not None:
        reference = relation['reference']
        previous, _ = _side(workspace, reference['task_id'], reference['run_id'], expected=reference)
        # The reference itself preserves the old run's kind even if its record is gone.
        if previous['kind'] is None:
            previous['kind'] = reference['kind']
        comparison = _difference(previous, current)
    else:
        comparison = dict(state=relation['state'], kind=None, files=[], truncated=False)
    result = dict(version=1, kind='artifact_versions', conversation_id=conversation_id,
        revision=revision, task_id=task_id, run_id=run_id, relation=relation,
        current=current, previous=previous, comparison=comparison)
    # Full originals stay intact. Only the derived display diff is shortened to
    # fit the existing response envelope (including JSON escaping).
    while _bytes(result) > 65536 and any(item['diff'] for item in comparison['files']):
        item = max(comparison['files'], key=lambda item: len(item['diff']))
        item['diff'] = item['diff'][:len(item['diff']) // 2]
        comparison['truncated'] = True
    return result
