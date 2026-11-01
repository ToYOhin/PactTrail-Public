"""Managed launch directories; never adopt or replay an old task executor."""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import re
import stat
import uuid

from pacttrail.application.chat import chat_error, safe_chat_error
from pacttrail.domain.draft_apply import target_path


_FORMAT = 'pacttrail.agent-workspace'
_TASK = re.compile(r'task-[0-9a-f]{32}\Z')
MAX_TASKS = 64
_MAX_CREATED_AT_MS = 253402300799999


def _plain(path, *, directory=False, limit=8192):
    meta = path.lstat()
    if (stat.S_ISLNK(meta.st_mode) or getattr(meta, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR(meta.st_mode) if directory else stat.S_ISREG(meta.st_mode))
            or not directory and (meta.st_nlink != 1 or meta.st_size > limit)):
        raise chat_error('history_unavailable')
    return meta.st_dev, meta.st_ino


def _created_at_ms(path, identity):
    """Capture directory creation metadata, never infer it from modification time."""
    meta = path.lstat()
    if (stat.S_ISLNK(meta.st_mode) or getattr(meta, 'st_file_attributes', 0) & 0x400
            or not stat.S_ISDIR(meta.st_mode) or (meta.st_dev, meta.st_ino) != identity):
        raise chat_error('history_unavailable')
    value = getattr(meta, 'st_birthtime_ns', None)
    # Older Windows Python exposes creation time through ctime; on other
    # platforms ctime is a metadata-change clock and cannot fill this field.
    if value is None and os.name == 'nt':
        value = getattr(meta, 'st_ctime_ns', None)
    if type(value) is not int:
        return None
    value //= 1_000_000
    return value if 0 <= value <= _MAX_CREATED_AT_MS else None


def _chain(path):
    for parent in reversed((path, *path.parents)):
        _plain(parent, directory=True)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise chat_error('history_corrupt')
        result[key] = value
    return result


def _create_file(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if os.write(descriptor, content) != len(content):
            raise chat_error('state_unavailable')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class AgentWorkspace:
    def __init__(self, workspace_dir, edit_project_dir=None):
        self._raw_root, self._raw_project = workspace_dir, edit_project_dir
        self._descriptor = None

    def _read_manifest(self):
        path = self.root / 'workspace.json'
        file_identity = _plain(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        try:
            meta = os.fstat(descriptor)
            if (meta.st_dev, meta.st_ino) != file_identity:
                raise chat_error('history_unavailable')
            payload = os.read(descriptor, 8193)
        finally:
            os.close(descriptor)
        value = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique)
        if (type(value) is not dict or set(value) != {'format', 'version', 'project_root', 'project_identity'}
                or value['format'] != _FORMAT or type(value['version']) is not int or value['version'] != 1):
            raise chat_error('history_corrupt')
        project, identity = value['project_root'], value['project_identity']
        if not ((project is None and identity is None) or (type(project) is str and type(identity) is list
                and len(identity) == 2 and all(type(item) is int and item >= 0 for item in identity))):
            raise chat_error('history_corrupt')
        if value != self._expected:
            raise chat_error('invalid_request')
        return file_identity

    def _acquire(self):
        path = self.root / 'workspace.lock'
        identity = _plain(path, limit=1)
        descriptor = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        try:
            meta = os.fstat(descriptor)
            if (meta.st_dev, meta.st_ino) != identity or meta.st_nlink != 1:
                raise chat_error('history_unavailable')
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise chat_error('history_in_use') from None
            if os.read(descriptor, 2) != b'\0':
                raise chat_error('history_unavailable')
            self._descriptor = descriptor
        except Exception:
            os.close(descriptor)
            raise

    def __enter__(self):
        if self._descriptor is not None:
            raise chat_error('history_in_use')
        try:
            self.root = target_path(os.fspath(self._raw_root))
            self.edit_project_dir = (target_path(os.fspath(self._raw_project))
                                     if self._raw_project is not None else None)
            _chain(self.root.parent)
            project_identity = None
            if self.edit_project_dir is not None:
                if (self.root == self.edit_project_dir or self.root in self.edit_project_dir.parents
                        or self.edit_project_dir in self.root.parents):
                    raise chat_error('invalid_request')
                _chain(self.edit_project_dir)
                project_identity = list(_plain(self.edit_project_dir, directory=True))
            self._expected = dict(format=_FORMAT, version=1,
                project_root=os.path.normcase(str(self.edit_project_dir)) if self.edit_project_dir is not None else None,
                project_identity=project_identity)
            try:
                self.root.lstat()
            except FileNotFoundError:
                self.root.mkdir()
                (self.root / 'tasks').mkdir()
                _create_file(self.root / 'workspace.lock', b'\0')
                _create_file(self.root / 'workspace.json', json.dumps(self._expected, sort_keys=True,
                    ensure_ascii=True, separators=(',', ':')).encode('ascii'))
            root_identity = _plain(self.root, directory=True)
            entries = list(itertools.islice(self.root.iterdir(), 5))
            allowed = {'workspace.json', 'workspace.lock', 'tasks', 'history'}
            if len(entries) > 4 or not {item.name for item in entries} <= allowed:
                raise chat_error('history_unavailable')
            manifest_identity = self._read_manifest()
            tasks = self.root / 'tasks'
            tasks_identity = _plain(tasks, directory=True)
            self.history_dir = self.root / 'history'
            try:
                self.history_dir.lstat()
            except FileNotFoundError:
                pass
            else:
                _plain(self.history_dir, directory=True)
            prior = list(itertools.islice(tasks.iterdir(), MAX_TASKS + 1))
            if len(prior) >= MAX_TASKS:
                raise chat_error('history_limit')
            prior_identities = {}
            prior_created_at = {}
            for item in prior:
                if _TASK.fullmatch(item.name) is None:
                    raise chat_error('history_unavailable')
                prior_identities[item.name[5:]] = _plain(item, directory=True)
                prior_created_at[item.name[5:]] = _created_at_ms(item, prior_identities[item.name[5:]])
            self._acquire()
            if (_plain(self.root, directory=True) != root_identity
                    or _plain(tasks, directory=True) != tasks_identity
                    or _plain(self.root / 'workspace.json') != manifest_identity):
                raise chat_error('history_unavailable')
            if self.edit_project_dir is not None and _plain(self.edit_project_dir, directory=True) != tuple(project_identity):
                raise chat_error('invalid_request')
            if any(_plain(tasks / ('task-' + task_id), directory=True) != identity
                   for task_id, identity in prior_identities.items()):
                raise chat_error('history_unavailable')
            self.task_dir = tasks / ('task-' + uuid.uuid4().hex)
            try:
                self.task_dir.lstat()
            except FileNotFoundError:
                self._history_root = root_identity
                self._history_tasks = tasks_identity
                self._history_manifest = manifest_identity
                self._history_leaves = prior_identities
                self._history_created_at = prior_created_at
                self._history_leaves[self.task_dir.name[5:]] = None
                return self
            raise chat_error('state_unavailable')
        except Exception as error:
            self.close()
            raise safe_chat_error(error) from None

    def _history_scope(self):
        if self._descriptor is None:
            raise chat_error('history_unavailable')
        try:
            _chain(self.root.parent)
            if (_plain(self.root, directory=True) != self._history_root
                    or _plain(self.root / 'tasks', directory=True) != self._history_tasks
                    or _plain(self.root / 'workspace.json') != self._history_manifest
                    or self._read_manifest() != self._history_manifest):
                raise chat_error('history_unavailable')
            for task_id, expected in self._history_leaves.items():
                actual = _plain(self.root / 'tasks' / ('task-' + task_id), directory=True)
                if expected is None:
                    created_at = _created_at_ms(self.root / 'tasks' / ('task-' + task_id), actual)
                    self._history_leaves[task_id] = actual
                    self._history_created_at[task_id] = created_at
                elif actual != expected:
                    raise chat_error('history_unavailable')
        except Exception:
            raise chat_error('history_unavailable') from None

    def history_tasks(self):
        """Only launch-registered leaves; do not enumerate new directories."""
        self._history_scope()
        return tuple(sorted(self._history_leaves))

    def history_groups(self):
        """Bounded captured directory metadata; no record-body reads or new leaves."""
        self._history_scope()
        current = self.task_dir.name[5:]
        groups = [dict(task_id=task_id, created_at_ms=created_at)
                  for task_id, created_at in self._history_created_at.items()]
        return tuple(sorted(groups, key=lambda item: (
            item['task_id'] != current, item['created_at_ms'] is None,
            -(item['created_at_ms'] or 0), item['task_id'])))

    def history_task_dir(self, task_id):
        """Resolve an opaque registered ID after rechecking its workspace anchors."""
        if (type(task_id) is not str or re.fullmatch(r'[a-f0-9]{32}', task_id) is None
                or task_id not in self._history_leaves):
            raise chat_error('invalid_request')
        self._history_scope()
        return self.root / 'tasks' / ('task-' + task_id)

    def close(self):
        if self._descriptor is not None:
            descriptor, self._descriptor = self._descriptor, None
            os.close(descriptor)  # The OS releases this process-owned lock, including on process exit.

    def __exit__(self, *unused):
        self.close()
