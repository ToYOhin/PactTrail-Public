"""Syntax-only checks of verified drafts; one automatic check per new Agent request."""
from __future__ import annotations

import copy
import re
import secrets
import threading

from pacttrail.application.chat import chat_error
from pacttrail.domain.draft_failure import format_draft_failure_goal, parse_draft_failure_goal
from pacttrail.domain.tool_task import DecisionError


class DraftPrecheckMixin:
    def _init_draft_precheck(self):
        self._draft_check = self._draft_binding = None
        self._draft_check_cancel = self._draft_check_worker = None
        self._next_draft_check = secrets.token_hex(16)
        self._draft_auto_request = None

    def _draft_check_busy(self):
        return self._draft_check is not None and (
            self._draft_check['status'] == 'running' or not self._draft_check['reaped'])

    @staticmethod
    def _draft_identity(state):
        """A check belongs to an outcome and source, not mutable page metadata."""
        task = state.get('recovered_draft') or state.get('task') or {}
        return (state.get('conversation_id'), (state.get('project') or {}).get('sha256'), task.get('run_id'),
                (task.get('artifact') or {}).get('sha256'))

    def _draft_check_state(self, state):
        if self._verifier is None:
            return state
        editing = state['editing']
        files = [dict(path=item['path'], status='pending', line=None)
                 for item in editing['files'] if item['path'].lower().endswith('.py')]
        identity = self._draft_identity(state)
        available = (not state['busy'] and editing['state'] == 'ready' and bool(files)
                     and (editing['run_id'], editing['artifact_sha256']) == identity[2:])
        check = copy.deepcopy(self._draft_check) if self._draft_check else dict(
            check_id=None, run_id=identity[2], artifact_sha256=identity[3], status='idle',
            exit_code=None, files=files, error=None, reaped=True)
        if self._draft_check and not self._draft_check_busy() and (
                identity != self._draft_binding or not available):
            check.update(status='stale', error='draft_precheck_stale')
        check.update(version=1, command='python_syntax_draft', available=available, next_id=self._next_draft_check)
        state['draft_precheck'] = check
        return state

    def _draft_failure_request(self, body):
        try:
            parsed = parse_draft_failure_goal(body['message'])
        except DecisionError:
            raise chat_error('invalid_request') from None
        if body.get('task_intent') != 'change_draft' or 'creation_path' in body:
            raise chat_error('invalid_request')
        state = self._session.status()
        identity = self._draft_identity(state)
        # Let the ordinary consumer reconcile an already accepted request. It
        # checks its original full payload and cannot dispatch a second worker.
        if (state.get('operation') or {}).get('id') == body.get('request_id'):
            return
        scope = (state.get('conversation') or {}).get('scope') or {}
        project = state.get('project') or {}
        check = self._project(state).get('draft_precheck')
        if (type(body.get('revision')) is not int or body['revision'] != state['revision']
                or body.get('conversation_id') != identity[0] or not state.get('conversation')
                or scope.get('state') != 'confirmed' or scope.get('sha256') != project.get('sha256')
                or body.get('project_sha256') != project.get('sha256')
                or (state.get('operation') or {}).get('status') != 'complete'
                or (state.get('task') or {}).get('intent') != 'change_draft'
                or identity != self._draft_binding or not check or not check['available']
                or check['status'] != 'failed' or type(check['exit_code']) is not int or check['exit_code'] != 1
                or check['reaped'] is not True or check['error'] is not None
                or (parsed['check_id'], parsed['run_id'], parsed['artifact_sha256']) != (
                    check['check_id'], identity[2], identity[3])):
            raise chat_error('stale_request')
        try:
            snapshot = self._editor.reviewed_snapshot(identity[2], identity[3])
            rows = check['files']
            if (type(rows) is not list or len(rows) != len(snapshot['files']) or not rows
                    or any(type(row) is not dict or set(row) != {'path', 'status', 'line'}
                        or row['path'] != entry['path'] or row['status'] not in ('passed', 'failed')
                        or (row['status'] == 'passed' and row['line'] is not None)
                        or (row['status'] == 'failed' and (type(row['line']) is not int
                            or not 1 <= row['line'] <= 257))
                        for row, entry in zip(rows, snapshot['files']))):
                raise ValueError()
            failures = [dict(path=row['path'], line=row['line']) for row in rows if row['status'] == 'failed']
            if format_draft_failure_goal(check['check_id'], identity[2], identity[3], failures) != body['message']:
                raise ValueError()
        except Exception:
            raise chat_error('stale_request') from None

    def _draft_check_action(self, body):
        base = self._session.status()
        state = self._project(base)
        if (self._verifier is None or type(body) is not dict or type(body.get('revision')) is not int
                or body.get('conversation_id') != state['conversation_id'] or body['revision'] != state['revision']
                or type(body.get('check_id')) is not str or re.fullmatch(r'[a-f0-9]{32}', body['check_id']) is None):
            raise chat_error('invalid_request')
        if body.get('action') == 'cancel':
            if (set(body) != {'conversation_id', 'revision', 'action', 'check_id'}
                    or not self._draft_check or body['check_id'] != self._draft_check['check_id']):
                raise chat_error('invalid_request')
            if self._draft_check_cancel is not None:
                self._draft_check_cancel.set()
            return self._project(base)
        if (set(body) != {'conversation_id', 'revision', 'action', 'check_id', 'run_id', 'artifact_sha256', 'confirmed'}
                or body.get('action') != 'start' or body.get('confirmed') is not True):
            raise chat_error('invalid_request')
        identity = (body['conversation_id'], self._draft_identity(state)[1],
                    body['run_id'], body['artifact_sha256'])
        if self._draft_check and body['check_id'] == self._draft_check['check_id'] and identity == self._draft_binding:
            if identity != self._draft_identity(state):
                raise chat_error('invalid_request')
            return self._project(base)  # Reconcile only, never launch twice.
        preview = state['draft_precheck']
        if (not preview['available'] or body['check_id'] != self._next_draft_check
                or identity != self._draft_identity(state)):
            raise chat_error('invalid_request')
        self._start_draft_check(identity, body['check_id'])
        return self._project(base)

    def _start_draft_check(self, identity, check_id):
        try:
            state = self._session.status()
            recovered = self._recovery(state)
            if recovered:
                state = dict(state, recovered_draft=recovered)
            if self._draft_identity(state) != identity:
                raise ValueError()
            editor = self._recovered_editor if recovered else self._editor
            snapshot = editor.reviewed_snapshot(identity[2], identity[3])
        except Exception:
            raise chat_error('draft_precheck_unavailable') from None
        self._draft_binding = identity
        self._draft_check = dict(check_id=check_id, run_id=identity[2],
            artifact_sha256=identity[3], status='running', exit_code=None,
            files=[dict(path=item['path'], status='pending', line=None) for item in snapshot['files']],
            error=None, reaped=True)
        self._next_draft_check = secrets.token_hex(16)
        self._draft_check_cancel = threading.Event()
        self._draft_check_worker = threading.Thread(target=self._run_draft_check,
            args=(snapshot, self._draft_check_cancel), daemon=True)
        try:
            self._draft_check_worker.start()
        except Exception:
            self._draft_check.update(status='error', error='draft_precheck_error')

    def _maybe_auto_draft_check(self, state):
        request = self._draft_auto_request
        if request is None:
            return
        identity = self._draft_identity(state)
        if self._closed or identity[:3] != request:
            self._draft_auto_request = None
            return
        if state['busy']:
            return  # The current model worker must finish and be reaped first.
        self._draft_auto_request = None  # Consume before any preparation or launch.
        if (self._verifier is None or (state.get('operation') or {}).get('status') != 'complete'
                or self._draft_binding is not None and self._draft_binding[:3] == request):
            return
        preview = self._project(state).get('draft_precheck')
        if not preview or not preview['available']:
            return
        check_id = self._next_draft_check
        try:
            self._start_draft_check(identity, check_id)
        except Exception:
            self._draft_binding = identity
            self._draft_check_cancel = self._draft_check_worker = None
            self._draft_check = dict(check_id=check_id, run_id=identity[2], artifact_sha256=identity[3],
                status='error', exit_code=None, files=[dict(item, status='not_run') for item in preview['files']],
                error='draft_precheck_error', reaped=True)
            self._next_draft_check = secrets.token_hex(16)

    def _run_draft_check(self, snapshot, cancel):
        try:
            result = self._verifier.run(snapshot, cancel)
            if (type(result) is not dict or set(result) != {'status', 'exit_code', 'files', 'error', 'reaped'}
                    or result['status'] not in ('passed', 'failed', 'cancelled', 'timed_out', 'error', 'unknown')
                    or type(result['reaped']) is not bool):
                raise ValueError()
        except Exception:
            result = dict(status='unknown', exit_code=None, files=[], error='verification_unknown', reaped=False)
        with self._lock:
            status = result['status']
            error = (None if status in ('passed', 'failed', 'cancelled') else
                     'draft_precheck_timeout' if status == 'timed_out' else
                     'draft_precheck_unknown' if not result['reaped'] else 'draft_precheck_error')
            self._draft_check.update(status=status, exit_code=result['exit_code'],
                                     files=result['files'], error=error, reaped=result['reaped'])
