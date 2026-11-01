"""Read-only recovery: no TaskFiles, model, credentials or worker construction."""
from __future__ import annotations

import copy
import json
import re
import threading

from pacttrail.application.chat import _bytes, chat_error
from pacttrail.domain.task_record import TaskRecordError
from pacttrail.domain.draft_apply import ApplyError
from pacttrail.platform.draft_apply_files import read_apply_receipt


class TaskHistorySession:
    def __init__(self, store):
        self._store = store
        self._lock = threading.RLock()
        self._closed = False
        self._selected = None

    def _project(self, *, report=False):
        selected, error = None, None
        records = []
        try:
            records = self._store.list_records()
            if self._selected is not None:
                selected = self._store.read_record(self._selected)
                if report:
                    selected['report'] = self._store.report(self._selected)
                application = read_apply_receipt(self._store.root, self._selected)
                if application is not None:
                    record = selected['record']
                    artifact = record['task']['artifact']
                    if (record['version'] not in (4, 6) or record['status'] != 'complete' or record['pending'] is not None
                            or record['error'] is not None or selected['integrity'] != 'verified'
                            or selected['recovery'] != 'terminal' or application['run_id'] != self._selected or artifact is None
                            or application['artifact_sha256'] != artifact['sha256']):
                        raise TaskRecordError('record_unavailable')
                    draft = json.loads((selected.get('report') or self._store.report(self._selected))['text'])
                    if ([(item['path'], item['sha256']) for item in application['files']]
                            != [(item['path'], item['new_sha256']) for item in draft['changes']]):
                        raise TaskRecordError('record_unavailable')
                    selected['application'] = application
        except (TaskRecordError, ApplyError) as failure:
            error = failure.code if isinstance(failure, TaskRecordError) else 'record_unavailable'
            selected = None
        result = dict(version=1, mode='task_history', read_only=True,
                      records=records, selected=selected, error=error)
        if _bytes(result) > 65536:
            raise chat_error('response_limit')
        return copy.deepcopy(result)

    def status(self):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            return self._project()

    def action(self, path, body):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            if (path != '/task-history' or type(body) is not dict
                    or type(body.get('action')) is not str or body['action'] not in ('list', 'open', 'report')
                    or set(body) != ({'action'} if body['action'] == 'list' else {'action', 'run_id'})):
                raise chat_error('invalid_request')
            if body['action'] != 'list':
                if type(body['run_id']) is not str or re.fullmatch(r'[a-f0-9]{32}', body['run_id']) is None:
                    raise chat_error('invalid_request')
                self._selected = body['run_id']
            else:
                self._selected = None
            return self._project(report=body['action'] == 'report')

    def close(self):
        with self._lock:
            self._closed = True
