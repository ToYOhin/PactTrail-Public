"""A managed-workspace viewer alongside, never inside, the live executor."""
from __future__ import annotations

import copy
import re
import threading

from pacttrail.application.chat import _bytes, chat_error
from pacttrail.application.task_history import TaskHistorySession
from pacttrail.domain.conversation_capacity import MAX_RESPONSE_BYTES
from pacttrail.platform.task_record_store import TaskRecordStore


class AgentTaskHistorySession:
    @property
    def response_byte_limit(self):
        return MAX_RESPONSE_BYTES

    def __init__(self, session, workspace):
        self._session, self._workspace = session, workspace
        self._lock = threading.RLock()
        self._closed = False
        self._groups = workspace.history_groups()
        self._tasks = tuple(sorted(item['task_id'] for item in self._groups))
        self._current = workspace.task_dir.name[5:]
        self._selected = None
        self._reader = None
        configure = getattr(session, 'set_artifact_reference_reader', None)
        if callable(configure):
            configure(self._read_artifact_reference)

    def _main(self, state):
        result = copy.deepcopy(state)
        result['agent_history_available'] = True
        operation, task = result.get('operation'), result.get('task')
        if (operation and task and operation.get('status') in ('stopped', 'error')
                and operation.get('id') == task.get('run_id')):
            result['continuation_target'] = dict(task_id=self._current, run_id=task['run_id'])
        if (operation and task and operation.get('status') in ('complete', 'stopped', 'error')
                and operation.get('id') == task.get('run_id') and task.get('artifact') is not None):
            result['artifact_target'] = dict(task_id=self._current, run_id=task['run_id'])
        if (operation and task and operation.get('status') in ('complete', 'stopped', 'error')
                and operation.get('id') == task.get('run_id')):
            result['source_target'] = dict(task_id=self._current, run_id=task['run_id'])
        return result

    def _read_artifact_reference(self, task_id, run_id):
        from pacttrail.application.artifact_reference import read_reference
        if task_id not in self._tasks:
            raise chat_error('invalid_request')
        return read_reference(self._workspace, task_id, run_id)

    def _artifact_reference_action(self, body):
        if type(body) is not dict or type(body.get('action')) is not str:
            raise chat_error('invalid_request')
        action = body['action']
        fields = {'action', 'conversation_id', 'revision'}
        if action in ('read', 'attach'):
            fields |= {'task_id', 'run_id'}
        if action == 'attach':
            fields |= {'record_sha256', 'artifact_sha256'}
        elif action == 'remove':
            fields.add('artifact_sha256')
        if (action not in ('read', 'attach', 'remove') or set(body) != fields
                or type(body['revision']) is not int or not 0 <= body['revision'] <= 2147483647):
            raise chat_error('invalid_request')
        for key in fields - {'action', 'revision'}:
            size = 64 if key.endswith('sha256') else 32
            if type(body[key]) is not str or re.fullmatch('[a-f0-9]{' + str(size) + '}', body[key]) is None:
                raise chat_error('invalid_request')
        state = self._session.read_binding() if action == 'read' else self._session.status()
        if (body['conversation_id'], body['revision']) != (state['conversation_id'], state['revision']):
            raise chat_error('stale_request')
        reference = None
        if action != 'remove':
            reference = self._read_artifact_reference(body['task_id'], body['run_id'])
        if action == 'read':
            result = dict(version=1, kind='artifact_reference_preview',
                conversation_id=body['conversation_id'], revision=body['revision'], reference=reference)
            if _bytes(result) > 65536:
                raise chat_error('response_limit')
            return result
        if action == 'attach' and (body['record_sha256'] != reference['record_sha256']
                or body['artifact_sha256'] != reference['artifact']['sha256']):
            raise chat_error('stale_request')
        return self._main(self._session.artifact_reference_action(body, reference))

    def _artifact_versions_action(self, body):
        from pacttrail.application.artifact_versions import artifact_versions

        fields = {'action', 'conversation_id', 'revision', 'task_id', 'run_id'}
        if (type(body) is not dict or set(body) != fields or body['action'] != 'read'
                or type(body['revision']) is not int or not 0 <= body['revision'] <= 2147483647):
            raise chat_error('invalid_request')
        for key in ('conversation_id', 'task_id', 'run_id'):
            if type(body[key]) is not str or re.fullmatch('[a-f0-9]{32}', body[key]) is None:
                raise chat_error('invalid_request')
        if body['task_id'] not in self._tasks:
            raise chat_error('invalid_request')
        state = self._session.read_binding()
        if (body['conversation_id'], body['revision']) != (state['conversation_id'], state['revision']):
            raise chat_error('stale_request')
        result = artifact_versions(self._workspace, body['task_id'], body['run_id'],
            conversation_id=body['conversation_id'], revision=body['revision'],
            legacy_reader=getattr(self._session, 'artifact_lineage_for_record', None))
        if _bytes(result) > 65536:
            raise chat_error('response_limit')
        return result

    def _source_evidence_action(self, body):
        from pacttrail.application.source_evidence import source_evidence

        fields = {'action', 'conversation_id', 'revision', 'task_id', 'run_id'}
        if (type(body) is not dict or set(body) != fields or body['action'] != 'read'
                or type(body['revision']) is not int or not 0 <= body['revision'] <= 2147483647):
            raise chat_error('invalid_request')
        for key in ('conversation_id', 'task_id', 'run_id'):
            if type(body[key]) is not str or re.fullmatch('[a-f0-9]{32}', body[key]) is None:
                raise chat_error('invalid_request')
        if body['task_id'] not in self._tasks:
            raise chat_error('invalid_request')
        state = self._session.read_binding()
        if (body['conversation_id'], body['revision']) != (state['conversation_id'], state['revision']):
            raise chat_error('stale_request')
        result = source_evidence(self._workspace, body['task_id'], body['run_id'],
            conversation_id=body['conversation_id'], revision=body['revision'])
        if _bytes(result) > 131072:
            raise chat_error('response_limit')
        return result

    def read_binding(self):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            return self._session.read_binding()

    def status(self):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
        return self._main(self._session.status())

    def _retire_reader(self):
        reader, self._reader = self._reader, None
        self._selected = None
        if reader is not None:
            reader.close()

    def _project(self, history=None, error=None):
        value = dict(version=2, mode='agent_history', read_only=True, tasks=list(self._tasks),
                     groups=list(self._groups),
                     current_task=self._current, selected_task=self._selected, history=history, error=error)
        if _bytes(value) > 65536:
            raise chat_error('response_limit')
        return copy.deepcopy(value)

    def _history_action(self, body):
        # Validate the entire closed request before resolving even one leaf.
        if type(body) is not dict or type(body.get('action')) is not str:
            raise chat_error('invalid_request')
        action = body['action']
        names = {'action'} if action == 'list' else {'action', 'task_id'}
        if action in ('open', 'report'):
            names.add('run_id')
        if action not in ('list', 'select', 'open', 'report') or set(body) != names:
            raise chat_error('invalid_request')
        for key in names - {'action'}:
            if type(body[key]) is not str or re.fullmatch(r'[a-f0-9]{32}', body[key]) is None:
                raise chat_error('invalid_request')
        if action != 'list' and body['task_id'] not in self._tasks:
            raise chat_error('invalid_request')
        try:
            if action == 'list':
                self._workspace.history_tasks()
                self._retire_reader()
                history = None
            else:
                task_id = body['task_id']
                root = self._workspace.history_task_dir(task_id)
                if task_id != self._selected:
                    self._retire_reader()
                    self._reader = TaskHistorySession(TaskRecordStore(root))
                    self._selected = task_id
                history = self._reader.action('/task-history', {'action': 'list'} if action == 'select'
                    else {'action': action, 'run_id': body['run_id']})
        except Exception:
            self._retire_reader()
            return self._project(error='history_unavailable')
        return self._project(history)

    def _continuation_action(self, body):
        from pacttrail.application.task_continuation import continuation_plan

        if type(body) is not dict or type(body.get('action')) is not str:
            raise chat_error('invalid_request')
        action = body['action']
        fields = {'action', 'conversation_id', 'revision', 'task_id', 'run_id'}
        if action == 'prepare':
            fields.add('record_sha256')
        if (action not in ('plan', 'prepare') or set(body) != fields
                or type(body['revision']) is not int or not 0 <= body['revision'] <= 2147483647):
            raise chat_error('invalid_request')
        for key in ('conversation_id', 'task_id', 'run_id'):
            if type(body[key]) is not str or re.fullmatch('[a-f0-9]{32}', body[key]) is None:
                raise chat_error('invalid_request')
        if (body['task_id'] not in self._tasks or action == 'prepare'
                and (type(body['record_sha256']) is not str
                     or re.fullmatch('[a-f0-9]{64}', body['record_sha256']) is None)):
            raise chat_error('invalid_request')
        state = self._session.read_binding() if action == 'plan' else self._session.status()
        if (body['conversation_id'], body['revision']) != (state['conversation_id'], state['revision']):
            raise chat_error('stale_request')
        reader = None
        try:
            root = self._workspace.history_task_dir(body['task_id'])
            reader = TaskHistorySession(TaskRecordStore(root))
            history = reader.action('/task-history', dict(action='open', run_id=body['run_id']))
            detail = history['selected']
            if detail is None or history['error'] is not None:
                raise chat_error('history_unavailable')
            if (detail['record']['task']['artifact'] is not None
                    and detail['integrity'] == 'verified' and detail['recovery'] != 'write_unknown'):
                history = reader.action('/task-history', dict(action='report', run_id=body['run_id']))
                detail = history['selected']
                if detail is None or history['error'] is not None:
                    raise chat_error('history_unavailable')
            plan = continuation_plan(detail, conversation_id=body['conversation_id'],
                revision=body['revision'], task_id=body['task_id'], busy=state['busy'])
        except Exception:
            raise chat_error('history_unavailable') from None
        finally:
            if reader is not None:
                reader.close()
        if action == 'plan':
            if _bytes(plan) > 65536:
                raise chat_error('response_limit')
            return plan
        if body['record_sha256'] != plan['record_sha256']:
            raise chat_error('stale_request')
        if not plan['can_prepare']:
            raise chat_error('busy' if plan['reason'] == 'busy' else 'invalid_request')
        result = self._session.action('/conversation', dict(action='prepare_continuation',
            conversation_id=body['conversation_id'], revision=body['revision']))
        return self._main(result)

    def action(self, path, body):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            if path == '/agent-history':
                return self._history_action(body)
            if path == '/continuation':
                return self._continuation_action(body)
            if path == '/artifact-reference':
                return self._artifact_reference_action(body)
            if path == '/artifact-versions':
                return self._artifact_versions_action(body)
            if path == '/source-evidence':
                return self._source_evidence_action(body)
        result = self._session.action(path, body)
        independent = (path == '/preview' or path == '/conversation'
                       and (result.get('kind') == 'conversation_archive' or result.get('mode') in ('history_reference', 'context_summary')) or path == '/project-locate'
                       and result.get('kind') in ('project_location', 'project_location_cancelled'))
        return result if independent else self._main(result)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._retire_reader()
            finally:
                self._session.close()
