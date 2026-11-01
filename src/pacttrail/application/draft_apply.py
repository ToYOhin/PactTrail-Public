"""Apply only the completed, verified draft explicitly reviewed by the user."""
from __future__ import annotations

import threading
import base64

from pacttrail.application.chat_project import from_upload
from pacttrail.domain.change_draft import validate_draft
from pacttrail.domain.draft_apply import ApplyError, run_id as valid_run_id, validate_apply_request


class DraftApplyService:
    def __init__(self, record_store, target_files):
        self._records = record_store
        self._files = target_files
        self._lock = threading.RLock()

    @property
    def target_root(self):
        return self._files.target_root

    def refresh_project(self, project, *, created_paths=()):
        return self._files.refresh_project(project, created_paths=created_paths)

    def _draft(self, run_id):
        try:
            detail = self._records.read_record(run_id)
            record = detail['record']
            if (record['run_id'] != run_id or record['version'] not in (4, 6) or record['status'] != 'complete'
                    or record['error'] is not None or record['pending'] is not None
                    or detail['integrity'] != 'verified' or detail['recovery'] != 'terminal'):
                raise ApplyError('draft_unavailable')
            artifact = self._records.report(run_id)
            if {key: artifact[key] for key in ('name', 'sha256', 'bytes')} != record['task']['artifact']:
                raise ApplyError('draft_unavailable')
            payload = validate_draft(artifact['text'], run_id, record['task']['source'])
            final = self._records.read_record(run_id)
            if final['record'] != record or final['integrity'] != 'verified' or final['recovery'] != 'terminal':
                raise ApplyError('draft_unavailable')
            return record['task']['source'], artifact, payload
        except Exception:
            raise ApplyError('draft_unavailable') from None

    def status(self, run_id=None):
        with self._lock:
            value = {'version': 1, 'target_root': str(self._files.target_root), 'run_id': None,
                     'artifact_sha256': None, 'state': 'idle', 'files': [], 'error': None}
            if run_id is None:
                return value
            valid_run_id(run_id)
            existing = self._files.receipt(run_id)
            if existing is not None:
                _, artifact, draft = self._draft(run_id)
                if (existing['artifact_sha256'] != artifact['sha256']
                        or [(item['path'], item['sha256']) for item in existing['files']]
                        != [(item['path'], item['new_sha256']) for item in draft['changes']]):
                    raise ApplyError('draft_unavailable')
                return existing
            _, artifact, draft = self._draft(run_id)
            value.update(run_id=run_id, artifact_sha256=artifact['sha256'], state='ready',
                files=[{'path': item['path'], 'state': 'pending', 'sha256': item['new_sha256']}
                       for item in draft['changes']])
            return value

    def verified_draft(self, run_id):
        """Return saved content only after the original terminal record verifies."""
        with self._lock:
            valid_run_id(run_id)
            source, artifact, _ = self._draft(run_id)
            return dict(run_id=run_id, source=source, artifact=artifact)

    def reviewed_snapshot(self, run_id, artifact_sha256):
        """Read only verified saved new texts, never client-supplied code."""
        with self._lock:
            try:
                valid_run_id(run_id)
                _, artifact, draft = self._draft(run_id)
                if artifact['sha256'] != artifact_sha256 or self._files.receipt(run_id) is not None:
                    raise ApplyError('draft_unavailable')
                files = [dict(path=item['path'], data=base64.b64encode(item['new_text'].encode('utf-8')).decode('ascii'))
                         for item in draft['changes'] if item['path'].lower().endswith('.py')]
                return from_upload('draft-precheck', files)
            except Exception:
                raise ApplyError('draft_unavailable') from None

    def apply(self, body):
        with self._lock:
            request = validate_apply_request(body)
            run_id, digest = request['run_id'], request['artifact_sha256']
            existing = self._files.receipt(run_id)
            if existing is not None:
                if existing['artifact_sha256'] != digest:
                    raise ApplyError('target_conflict')
                return existing
            source, artifact, payload = self._draft(run_id)
            if artifact['sha256'] != digest:
                raise ApplyError('invalid_request')
            options = {'draft_version': 2} if payload['version'] == 2 else {}
            return self._files.apply(run_id, digest, source, payload['changes'], **options)
