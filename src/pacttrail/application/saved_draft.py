"""Resolve an explicitly opened saved draft without restoring its executor."""
from __future__ import annotations

import copy

from pacttrail.application.draft_apply import DraftApplyService
from pacttrail.domain.draft_apply import ApplyError, run_id, target_path, validate_apply_receipt
from pacttrail.domain.task_record import TaskRecordError
from pacttrail.platform.draft_apply_files import DraftApplyFiles, read_apply_receipt
from pacttrail.platform.task_record_store import TaskRecordStore


class SavedDraftResolver:
    """Bind the shown conversation result to its unique registered task leaf."""

    def __init__(self, workspace, target_root):
        self._workspace = workspace
        self._target = target_path(str(target_root))
        if workspace.edit_project_dir != self._target:
            raise ApplyError('draft_unavailable')

    def _locate(self, result):
        try:
            if (type(result) is not dict or set(result) != {
                    'run_id', 'source', 'artifact', 'application'}
                    or type(result['artifact']) is not dict
                    or set(result['artifact']) != {'name', 'sha256', 'bytes', 'text'}):
                raise ValueError()
            run_id(result['run_id'])
            if result['application'] is not None:
                application = validate_apply_receipt(result['application'])
                if application['state'] != 'applied':
                    raise ValueError()
            groups = self._workspace.history_tasks()
            found = None
            for task_id in groups:
                root = self._workspace.history_task_dir(task_id)
                store = TaskRecordStore(root)
                try:
                    detail = store.read_record(result['run_id'])
                except TaskRecordError as error:
                    if error.code == 'record_missing':
                        continue
                    raise
                record = detail['record']
                if (found is not None or detail['recovery'] != 'terminal'
                        or detail['integrity'] != 'verified' or record['version'] not in (4, 6)
                        or record['status'] != 'complete' or record['error'] is not None
                        or record['pending'] is not None or record['task']['source'] != result['source']
                        or record['task']['artifact'] != {
                            key: result['artifact'][key] for key in ('name', 'sha256', 'bytes')}
                        or store.report(result['run_id']) != result['artifact']):
                    raise ValueError()
                receipt = read_apply_receipt(root, result['run_id'])
                if (receipt is not None and receipt['state'] != 'applied'
                        or result['application'] is not None and result['application'] != receipt):
                    raise ValueError()
                found = task_id, root, store
            if (found is None or self._workspace.history_tasks() != groups
                    or self._workspace.history_task_dir(found[0]) != found[1]):
                raise ValueError()
            return found
        except Exception:
            raise ApplyError('draft_unavailable') from None

    def __call__(self, result):
        saved = copy.deepcopy(result)
        task_id, root, store = self._locate(saved)
        service = _SavedDraftService(self, saved, task_id, store,
                                     DraftApplyFiles(self._target, root))
        if service.verified_draft(saved['run_id']) != {
                key: saved[key] for key in ('run_id', 'source', 'artifact')}:
            raise ApplyError('draft_unavailable')
        service.status(saved['run_id'])  # Validate any existing receipt against the exact draft.
        return service


class _SavedDraftService(DraftApplyService):
    """Keep the workspace registration and original result bound through apply."""

    def __init__(self, resolver, result, task_id, store, files):
        super().__init__(store, files)
        self._resolver, self._saved, self._task_id = resolver, result, task_id

    def _scope(self, requested=None):
        task_id, root, _ = self._resolver._locate(self._saved)
        if (task_id != self._task_id or root != self._records.root
                or requested is not None and requested != self._saved['run_id']):
            raise ApplyError('draft_unavailable')

    def status(self, run_id=None):
        with self._lock:
            self._scope(run_id)
            state = super().status(run_id)
            if state['state'] not in ('idle', 'ready', 'applied'):
                raise ApplyError('draft_unavailable')
            return state

    def verified_draft(self, run_id):
        with self._lock:
            self._scope(run_id)
            return super().verified_draft(run_id)

    def reviewed_snapshot(self, run_id, artifact_sha256):
        with self._lock:
            self._scope(run_id)
            return super().reviewed_snapshot(run_id, artifact_sha256)

    def apply(self, body):
        with self._lock:
            if type(body) is not dict:
                raise ApplyError('draft_unavailable')
            self._scope(body.get('run_id'))
            self.status(self._saved['run_id'])
            return super().apply(body)

    def refresh_project(self, project, *, created_paths=()):
        with self._lock:
            self._scope()
            return super().refresh_project(project, created_paths=created_paths)
