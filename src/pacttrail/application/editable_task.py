"""Reviewed edits alongside an existing conversation, without replaying its task."""
from __future__ import annotations

import copy
import re
import secrets
import threading

from pacttrail.application.chat import chat_error
from pacttrail.application.chat_project import validate_saved
from pacttrail.application.draft_precheck import DraftPrecheckMixin
from pacttrail.domain.draft_apply import ApplyError
from pacttrail.domain.draft_failure import DRAFT_FAILURE_PREFIX
from pacttrail.domain.syntax_repair import REPAIR_PREFIX, format_repair_goal, parse_repair_goal
from pacttrail.domain.tool_task import DecisionError


class EditableTaskSession(DraftPrecheckMixin):
    @property
    def response_byte_limit(self):
        return self._session.response_byte_limit

    def __init__(self, session, editor, verifier=None, *, saved_draft_resolver=None,
                 project_locator=None):
        self._session = session
        self._editor = editor
        self._saved_draft_resolver = saved_draft_resolver
        self._recovered_draft = None
        self._recovered_editor = None
        self._recovered_conversation = None
        self._lock = threading.RLock()
        self._closed = False
        self._failure = None
        self._verifier = verifier
        self._verification = None
        self._verification_binding = None
        self._verification_snapshot = None
        self._project_location = None
        if project_locator is not None:
            from pacttrail.application.project_location import BoundProjectLocation
            self._project_location = BoundProjectLocation(project_locator)
        self._next_check = secrets.token_hex(16)
        self._check_cancel = None
        self._check_worker = None
        self._init_draft_precheck()
        reader = getattr(self._session, 'set_project_reader', None)
        if callable(reader):
            reader(editor.refresh_project, target_root=str(editor.target_root))

    def set_artifact_reference_reader(self, reader):
        with self._lock:
            self._session.set_artifact_reference_reader(reader)
            return self

    def artifact_reference_action(self, body, reference=None):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            if self._draft_check_busy() or (self._verification is not None and (
                    self._verification['status'] == 'running' or not self._verification['reaped'])):
                raise chat_error('busy')
            return self._project(self._session.artifact_reference_action(body, reference))

    def artifact_lineage_for_record(self, record):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            return self._session.artifact_lineage_for_record(record)

    def _recovery(self, state):
        recovered = self._recovered_draft
        if recovered is None:
            return None
        result = (state.get('conversation') or {}).get('result')
        project = state.get('project') or {}
        if (state.get('conversation_id') != self._recovered_conversation
                or (state.get('task') or {}).get('run_id') is not None or state.get('busy')
                or not result or any(result.get(key) != recovered[key] for key in recovered)
                or project.get('sha256') != recovered['source']['sha256']):
            self._recovered_draft = self._recovered_editor = self._recovered_conversation = None
            return None
        return recovered

    def _recover_draft(self, body):
        state = self._session.status()
        result = (state.get('conversation') or {}).get('result')
        scope = (state.get('conversation') or {}).get('scope') or {}
        if (self._saved_draft_resolver is None or type(body) is not dict
                or set(body) != {'action', 'conversation_id', 'revision', 'run_id', 'artifact_sha256'}
                or type(body.get('revision')) is not int or body['revision'] != state['revision']
                or body.get('conversation_id') != state['conversation_id'] or state['busy']
                or self._draft_check_busy() or (state.get('task') or {}).get('run_id') is not None
                or self._verification is not None and (
                    self._verification['status'] == 'running' or not self._verification['reaped'])
                or scope.get('state') != 'confirmed' or not result
                or body.get('run_id') != result.get('run_id')
                or body.get('artifact_sha256') != (result.get('artifact') or {}).get('sha256')):
            raise chat_error('stale_request')
        project = copy.deepcopy(state.get('project'))
        if project:
            for item in project['files']:
                item.pop('text', None)
        if not project or result.get('source') != project:
            raise chat_error('stale_request')
        try:
            editor = self._saved_draft_resolver(copy.deepcopy(result))
            recovered = editor.verified_draft(result['run_id'])
            if (recovered != {key: result[key] for key in ('run_id', 'source', 'artifact')}
                    or str(editor.target_root) != str(self._editor.target_root)):
                raise ApplyError('draft_unavailable')
            editor.status(result['run_id'])
        except Exception:
            raise chat_error('record_report_unavailable') from None
        self._recovered_draft, self._recovered_editor = recovered, editor
        self._recovered_conversation = state['conversation_id']
        self._failure = None
        return self._project(state)

    def _project(self, state):
        state = copy.deepcopy(state)
        checking = self._verification is not None and (
            self._verification['status'] == 'running' or not self._verification['reaped'])
        state['busy'] = state['busy'] or checking
        # A fresh precheck owns its own busy state, not a restored executor.
        recovered = self._recovery(state)
        state['busy'] = state['busy'] or self._draft_check_busy()
        task = state.get('task') or {}
        run_id = task.get('run_id')
        eligible = (task.get('intent') == 'change_draft'
                    and (state.get('operation') or {}).get('status') == 'complete' and not state['busy'])
        editor = self._recovered_editor if recovered else self._editor
        if recovered:
            run_id = recovered['run_id']
        if self._saved_draft_resolver is not None:
            state['recovered_draft'] = copy.deepcopy(recovered)
        try:
            editing = editor.status(run_id if eligible or recovered else None)
        except ApplyError as error:
            editing = dict(version=1, target_root=str(self._editor.target_root), run_id=None,
                           artifact_sha256=None, state='unavailable', files=[], error=error.code)
        if self._failure is not None and self._failure[0] == run_id and editing['state'] in ('idle', 'ready'):
            editing.update(state='unavailable', error=self._failure[1],
                           files=[dict(item, state='not_attempted') for item in editing['files']])
        state['editing'] = editing
        if self._verifier is not None:
            project = state.get('project') or {}
            files = [dict(path=item['path'], status='pending', line=None)
                     for item in project.get('files', []) if item['path'].lower().endswith('.py')]
            scope = (state.get('conversation') or {}).get('scope') or {}
            available = (not state['busy'] and bool(files) and project.get('version') == 1
                         and scope.get('state') == 'confirmed'
                         and scope.get('sha256') == project.get('sha256'))
            check = copy.deepcopy(self._verification) if self._verification is not None else dict(
                check_id=None, project_sha256=project.get('sha256'), status='idle',
                exit_code=None, files=files, error=None, reaped=True)
            if (check['status'] not in ('idle', 'running', 'unknown') and
                    (check['project_sha256'] != project.get('sha256')
                     or self._verification_binding[0] != state['conversation_id'])):
                check.update(status='stale', error='verification_stale')
            check.update(version=1, command='python_syntax', target_root=str(self._editor.target_root),
                         available=available, next_id=self._next_check)
            state['verification'] = check
        return self._draft_check_state(state)

    def _check_action(self, body):
        state = self._session.status()
        if (self._verifier is None or type(body) is not dict
                or type(body.get('revision')) is not int
                or body.get('conversation_id') != state['conversation_id']
                or body['revision'] != state['revision']
                or type(body.get('check_id')) is not str
                or re.fullmatch(r'[a-f0-9]{32}', body['check_id']) is None):
            raise chat_error('invalid_request')
        if body.get('action') == 'cancel':
            if (set(body) != {'conversation_id', 'revision', 'action', 'check_id'}
                    or self._verification is None or body['check_id'] != self._verification['check_id']):
                raise chat_error('invalid_request')
            self._check_cancel.set()
            return self._project(state)
        if (set(body) != {'conversation_id', 'revision', 'action', 'check_id',
                         'project_sha256', 'confirmed'} or body['action'] != 'start'
                or body['confirmed'] is not True or type(body['project_sha256']) is not str
                or re.fullmatch(r'[a-f0-9]{64}', body['project_sha256']) is None):
            raise chat_error('invalid_request')
        binding = (state['conversation_id'], state['revision'], body['project_sha256'])
        if (self._verification is not None and body['check_id'] == self._verification['check_id']
                and binding == self._verification_binding):
            return self._project(state)  # Reconcile a lost response without a second launch.
        preview = self._project(state)['verification']
        if (not preview['available'] or body['check_id'] != self._next_check
                or body['project_sha256'] != (state.get('project') or {}).get('sha256')):
            raise chat_error('invalid_request')
        try:
            project = self._editor.refresh_project(state['project'])
            if project['sha256'] != body['project_sha256']:
                raise ValueError()
        except Exception:
            raise chat_error('invalid_request') from None
        self._verification = dict(check_id=body['check_id'], project_sha256=project['sha256'],
                                  status='running', exit_code=None, files=preview['files'],
                                  error=None, reaped=False)
        self._verification_binding = binding
        self._verification_snapshot = copy.deepcopy(project)
        self._next_check = secrets.token_hex(16)
        self._check_cancel = threading.Event()
        worker = threading.Thread(target=self._run_check,
                                  args=(copy.deepcopy(project), self._check_cancel), daemon=True)
        self._check_worker = worker
        try:
            worker.start()
        except Exception:
            self._verification.update(status='error', error='verification_error', reaped=True)
        return self._project(state)

    def _run_check(self, project, cancel):
        try:
            result = self._verifier.run(project, cancel)
        except Exception:
            # A broken adapter cannot establish whether its child was reaped.
            result = dict(status='unknown', exit_code=None, files=[],
                          error='verification_unknown', reaped=False)
        with self._lock:
            status = result['status']
            error = (None if status in ('passed', 'failed', 'cancelled') else
                     'verification_timeout' if status == 'timed_out' else
                     'verification_unknown' if not result['reaped'] else 'verification_error')
            self._verification.update(status=status, exit_code=result['exit_code'],
                                      files=result['files'], error=error, reaped=result['reaped'])
            if not self._closed and status in ('passed', 'failed'):
                try:
                    current = self._editor.refresh_project(project)
                    stale = current['sha256'] != project['sha256']
                except Exception:
                    stale = True
                if stale:
                    self._verification.update(status='stale', error='verification_stale')

    def read_binding(self):
        """Project cached editor occupancy without starting or polling checks."""
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            binding = dict(self._session.read_binding())
            checking = self._verification is not None and (
                self._verification['status'] == 'running' or not self._verification['reaped'])
            binding['busy'] = bool(binding['busy'] or checking or self._draft_check_busy())
            return binding

    def status(self):
        with self._lock:
            state = self._session.status()
            self._maybe_auto_draft_check(state)
            return self._project(state)

    def _repair_request(self, body):
        try:
            parsed = parse_repair_goal(body['message'])
        except DecisionError:
            raise chat_error('invalid_request') from None
        if body.get('task_intent') != 'change_draft' or 'creation_path' in body:
            raise chat_error('invalid_request')
        state = self._session.status()
        project = state.get('project') or {}
        scope = (state.get('conversation') or {}).get('scope') or {}
        check, captured, binding = self._verification, self._verification_snapshot, self._verification_binding
        if (type(body.get('revision')) is not int or body['revision'] != state['revision']
                or body.get('conversation_id') != state['conversation_id']
                or scope.get('state') != 'confirmed' or scope.get('sha256') != project.get('sha256')
                or body.get('project_sha256') != project.get('sha256')
                or parsed['project_sha256'] != project.get('sha256')
                or check is None or captured is None or binding is None
                or binding[0] != state['conversation_id'] or binding[2] != project.get('sha256')
                or check['status'] != 'failed' or type(check['exit_code']) is not int or check['exit_code'] != 1
                or check['reaped'] is not True or check['error'] is not None
                or check['project_sha256'] != project.get('sha256')):
            raise chat_error('stale_request')
        try:
            captured = validate_saved(captured)
            if captured['version'] != 1 or captured != project:
                raise ValueError()
            # The current confirmed snapshot and selected target files must still
            # be exactly the ones checked; this reads no unselected paths.
            if self._editor.refresh_project(captured) != captured:
                raise ValueError()
            entries = [entry for entry in captured['files'] if entry['path'].lower().endswith('.py')]
            if (type(check['files']) is not list or len(check['files']) != len(entries)
                    or [item['path'] for item in check['files']] != [entry['path'] for entry in entries]):
                raise ValueError()
            failures = []
            for entry, result in zip(entries, check['files']):
                if (type(result) is not dict or set(result) != {'path', 'status', 'line'}
                        or result['status'] not in ('passed', 'failed')
                        or result['status'] == 'passed' and result['line'] is not None):
                    raise ValueError()
                if result['status'] == 'failed':
                    failures.append(dict(file_id=entry['file_id'], line=result['line']))
            expected = format_repair_goal(check['check_id'], captured['sha256'], failures)
            if expected != body['message']:
                raise ValueError()
        except Exception:
            raise chat_error('stale_request') from None

    def action(self, path, body):
        with self._lock:
            if self._closed:
                raise chat_error('state_unavailable')
            if path == '/project-locate':
                if self._project_location is None:
                    raise chat_error('project_location_unavailable')
                if self._draft_check_busy() or (self._verification is not None and (
                        self._verification['status'] == 'running' or not self._verification['reaped'])):
                    raise chat_error('busy')
                # Bypass this wrapper's status projection: locating never starts
                # automatic syntax work or restores an executor.
                result = self._project_location.action(body, self._session.status(),
                    lambda fields: self._session.action('/project', fields))
                return self._project(result) if body['action'] == 'attach' else result
            if path == '/preview':
                # A status projection can start automatic draft syntax work;
                # preview deliberately bypasses both status and editor reads.
                result = self._session.action(path, body)
                if (self._draft_check_busy() or self._verification is not None and (
                        self._verification['status'] == 'running' or not self._verification['reaped'])):
                    if 'busy' not in result['blockers']:
                        result['blockers'].append('busy')
                return result
            if path == '/conversation' and type(body) is dict and body.get('action') in ('read_archive', 'read_history', 'summary_candidate'):
                # Independent read-only history does not project an editor or
                # start its automatic checks for the currently selected task.
                return self._session.action(path, body)
            if path == '/verify':
                return self._check_action(body)
            if path == '/conversation' and type(body) is dict and body.get('action') == 'recover_draft':
                return self._recover_draft(body)
            if path == '/draft-precheck':
                return self._draft_check_action(body)
            if (path == '/send' and type(body) is dict and type(body.get('message')) is str
                    and body['message'].startswith(REPAIR_PREFIX)):
                self._repair_request(body)
            if (path == '/send' and type(body) is dict and type(body.get('message')) is str
                    and body['message'].startswith(DRAFT_FAILURE_PREFIX)):
                self._draft_failure_request(body)
            if self._draft_check_busy() or (self._verification is not None and (
                    self._verification['status'] == 'running' or not self._verification['reaped'])):
                raise chat_error('busy')
            if path != '/draft-apply':
                previous = (self._session.status().get('task') or {}).get('run_id') if path == '/send' else None
                state = self._session.action(path, body)
                task = state.get('task') or {}
                if (path == '/send' and state.get('conversation') is not None and type(body) is dict
                        and task.get('intent') == 'change_draft' and task.get('run_id') == body.get('request_id')
                        and (state.get('operation') or {}).get('id') == body.get('request_id')
                        and previous != body.get('request_id')):
                    self._draft_auto_request = self._draft_identity(state)[:3]
                    self._maybe_auto_draft_check(state)
                return self._project(state)
            state = self._session.status()
            recovered = self._recovery(state)
            task = ({'intent': 'change_draft', **recovered} if recovered else state.get('task') or {})
            if (type(body) is not dict or set(body) != {
                    'conversation_id', 'revision', 'run_id', 'artifact_sha256', 'confirmed'}
                    or type(body['revision']) is not int or type(body['confirmed']) is not bool
                    or body['confirmed'] is not True
                    or body['conversation_id'] != state['conversation_id']
                    or body['revision'] != state['revision'] or state['busy']
                    or not recovered and (state.get('operation') or {}).get('status') != 'complete'
                    or task.get('intent') != 'change_draft'
                    or body['run_id'] != task.get('run_id')
                    or body['artifact_sha256'] != (task.get('artifact') or {}).get('sha256')):
                raise chat_error('invalid_request')
            try:
                editor = self._recovered_editor if recovered else self._editor
                if recovered:
                    if editor.verified_draft(body['run_id']) != recovered or editor.status(body['run_id'])['state'] != 'ready':
                        raise ApplyError('draft_unavailable')
                if state.get('conversation') is not None:
                    self._session.prepare_application(body['run_id'], body['artifact_sha256'])
                receipt = editor.apply({key: body[key] for key in ('run_id', 'artifact_sha256', 'confirmed')})
                if state.get('conversation') is not None:
                    self._session.record_application(receipt)
                self._failure = None
            except ApplyError as error:
                self._failure = (body['run_id'], error.code)
            return self._project(self._session.status())

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._project_location is not None:
                self._project_location.clear()
            self._draft_auto_request = None
            if self._check_cancel is not None:
                self._check_cancel.set()
            worker = self._check_worker
            if self._draft_check_cancel is not None:
                self._draft_check_cancel.set()
            draft_worker = self._draft_check_worker
        if worker is not None and worker.ident is not None:
            worker.join(timeout=3)
        if draft_worker is not None and draft_worker.ident is not None:
            draft_worker.join(timeout=3)
        self._session.close()
