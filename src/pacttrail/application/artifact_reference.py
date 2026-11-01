"""Read only registered task artifacts and bind their verified full contents."""
from __future__ import annotations

from pacttrail.application.chat import chat_error
from pacttrail.domain.artifact_reference import validate_reference
from pacttrail.domain.task_record import record_digest
from pacttrail.platform.task_record_store import TaskRecordStore


def read_reference(workspace, task_id, run_id):
    try:
        store = TaskRecordStore(workspace.history_task_dir(task_id))
        detail = store.read_record(run_id)
        if (detail['integrity'] not in ('verified', 'source_changed')
                or detail['recovery'] == 'write_unknown' or detail['record']['task']['artifact'] is None):
            raise ValueError('unverified artifact')
        record = detail['record']
        report = store.report(run_id, historical_reference=True)
        if store.read_record(run_id)['record'] != record:
            raise ValueError('changed record')
        source = record['task']['source']
        return validate_reference(dict(version=1, task_id=task_id, run_id=run_id,
            record_sha256=record_digest(record), goal=record['goal'],
            kind='change_draft' if record['version'] in (4, 6) else 'report',
            artifact=report, source=None if source is None else {
                key: source[key] for key in ('kind', 'name', 'sha256')}, state='review_required'))
    except Exception:
        raise chat_error('record_report_unavailable') from None
