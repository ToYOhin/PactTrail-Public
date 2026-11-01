"""Bounded replacements and exclusive creation under a fixed selected local root.

Checks detect links and path replacement under trusted single-operator ownership;
they do not provide an OS sandbox against a hostile local account. Each file is
atomic individually. A batch is not atomic and an uncertain write is not replayed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import threading

from pacttrail.domain.change_draft import validate_draft
from pacttrail.domain.draft_apply import (
    ApplyError, artifact_hash, canonical_receipt, run_id as valid_run_id,
    target_path, validate_apply_receipt,
)
from pacttrail.domain.project_source import validate_project_path


def _plain(metadata, *, directory=False):
    if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR if directory else stat.S_ISREG)(metadata.st_mode)
            or not directory and metadata.st_nlink != 1
            or type(metadata.st_ino) is not int or metadata.st_ino <= 0):
        raise ApplyError('unsafe_path')
    return metadata.st_dev, metadata.st_ino


def _version(metadata):
    return metadata.st_size, metadata.st_mtime_ns


def _anchors(root):
    result = {}
    for path in reversed((root, *root.parents)):
        result[path] = _plain(path.lstat(), directory=True)
    if root.resolve(strict=True) != root:
        raise ApplyError('unsafe_path')
    return result


def _check(anchors):
    for path, identity in anchors.items():
        if _plain(path.lstat(), directory=True) != identity:
            raise ApplyError('unsafe_path')


def _directory(path, anchors):
    identity = _plain(path.lstat(), directory=True)
    if identity != anchors.setdefault(path, identity):
        raise ApplyError('unsafe_path')


def _read(path, anchors, *, code='target_conflict'):
    _check(anchors)
    before = path.lstat()
    identity = _plain(before)
    version = _version(before)
    if not 0 < before.st_size <= 8192:
        raise ApplyError(code)
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if _plain(opened) != identity or _version(opened) != version:
            raise ApplyError(code)
        raw = stream.read(before.st_size + 1)
        after = os.fstat(stream.fileno())
        if _plain(after) != identity or _version(after) != version or len(raw) != before.st_size:
            raise ApplyError(code)
    final = path.lstat()
    if _plain(final) != identity or _version(final) != version:
        raise ApplyError(code)
    _check(anchors)
    return raw, identity, before


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ApplyError('apply_unavailable')
        result[key] = value
    return result


def _receipt_path(root, run):
    return root / 'applications' / ('apply-' + run + '.json')


def _read_receipt(root, run, anchors):
    directory = root / 'applications'
    try:
        _directory(directory, anchors)
    except FileNotFoundError:
        return None
    path = _receipt_path(root, run)
    try:
        path.with_suffix('.tmp').lstat()
    except FileNotFoundError:
        pass
    else:
        raise ApplyError('apply_unavailable')
    try:
        raw, _, _ = _read(path, anchors, code='apply_unavailable')
    except FileNotFoundError:
        return None
    value = validate_apply_receipt(json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs))
    if (value['run_id'] != run or value['state'] in ('idle', 'ready')
            or canonical_receipt(value) != raw):
        raise ApplyError('apply_unavailable')
    for item in value['files']:
        if item['state'] == 'pending':
            item['state'] = 'unknown'
    return value


def read_apply_receipt(task_root, run_id):
    """Read fixed application facts without opening a target path or repairing files."""
    try:
        root = target_path(os.fspath(task_root))
        valid_run_id(run_id)
        return _read_receipt(root, run_id, _anchors(root))
    except Exception:
        raise ApplyError('apply_unavailable') from None


def preflight_edit_roots(target_root, task_root):
    """Check disjoint local roots before creating TaskFiles; perform no writes."""
    try:
        target = target_path(os.fspath(target_root))
        task = target_path(os.fspath(task_root))
        if target == task or target in task.parents or task in target.parents:
            raise ApplyError('unsafe_path')
        _anchors(target)
        try:
            task.lstat()
        except FileNotFoundError:
            # TaskFiles creates only its exclusive leaf directory. Its parent
            # must already exist and every existing ancestor must be plain.
            _anchors(task.parent)
        else:
            _anchors(task)
    except ApplyError:
        raise
    except Exception:
        raise ApplyError('unsafe_path') from None


class DraftApplyFiles:
    def __init__(self, target_root, task_root):
        self._lock = threading.RLock()
        try:
            self.target_root = target_path(os.fspath(target_root))
            self.task_root = target_path(os.fspath(task_root))
            if (self.target_root == self.task_root or self.target_root in self.task_root.parents
                    or self.task_root in self.target_root.parents):
                raise ApplyError('unsafe_path')
            self._target_anchors = _anchors(self.target_root)
            self._task_anchors = _anchors(self.task_root)
            directory = self.task_root / 'applications'
            try:
                directory.mkdir()
            except FileExistsError:
                pass
            _directory(directory, self._task_anchors)
            _check(self._task_anchors)
        except ApplyError:
            raise
        except Exception:
            raise ApplyError('unsafe_path') from None

    def receipt(self, run_id):
        with self._lock:
            try:
                valid_run_id(run_id)
                value = _read_receipt(self.task_root, run_id, self._task_anchors)
                if value is not None and Path(value['target_root']) != self.target_root:
                    raise ApplyError('target_conflict')
                return value
            except ApplyError:
                raise
            except Exception:
                raise ApplyError('apply_unavailable') from None

    def _target(self, relative):
        path = self.target_root / relative
        for parent in reversed(path.parent.parents):
            if parent == self.target_root or self.target_root in parent.parents:
                _directory(parent, self._target_anchors)
        _directory(path.parent, self._target_anchors)
        return path

    def refresh_project(self, project, *, created_paths=()):
        """Read selected paths plus explicitly supplied, known-created paths only."""
        from pacttrail.application.chat_project import from_upload, validate_saved
        import base64
        with self._lock:
            try:
                validate_saved(project)
                if project['version'] != 1 or type(created_paths) not in (tuple, list):
                    raise ApplyError('unsafe_path')
                paths = [entry['path'] for entry in project['files']]
                for relative in created_paths:
                    validate_project_path(relative)
                    paths.append(relative)
                if len(paths) > 4 or len({path.casefold() for path in paths}) != len(paths):
                    raise ApplyError('unsafe_path')
                files = []
                for relative in paths:
                    raw, _, _ = _read(self._target(relative), self._target_anchors)
                    files.append(dict(path=relative, data=base64.b64encode(raw).decode('ascii')))
                return from_upload(project['name'], files)
            except ApplyError:
                raise
            except Exception:
                raise ApplyError('target_conflict') from None

    @staticmethod
    def _remove_owned(path, identity, anchors):
        if identity is None:
            return
        try:
            _check(anchors)
            if _plain(path.lstat()) == identity:
                path.unlink()
        except Exception:
            pass

    def _persist(self, value, *, first=False):
        value = validate_apply_receipt(value)
        path = _receipt_path(self.task_root, value['run_id'])
        temp, identity = path.with_suffix('.tmp'), None
        try:
            _check(self._task_anchors)
            try:
                prior = _plain(path.lstat())
            except FileNotFoundError:
                prior = None
            if first and prior is not None:
                raise ApplyError('apply_unavailable')
            with temp.open('xb') as stream:
                identity = _plain(os.fstat(stream.fileno()))
                raw = canonical_receipt(value)
                if stream.write(raw) != len(raw):
                    raise ApplyError('apply_unavailable')
                stream.flush()
                os.fsync(stream.fileno())
            _check(self._task_anchors)
            if _plain(temp.lstat()) != identity:
                raise ApplyError('apply_unavailable')
            try:
                current = _plain(path.lstat())
            except FileNotFoundError:
                current = None
            if current != prior:
                raise ApplyError('apply_unavailable')
            os.replace(temp, path)
            actual, _, _ = _read(path, self._task_anchors, code='apply_unavailable')
            if actual != raw:
                raise ApplyError('apply_unavailable')
        finally:
            self._remove_owned(temp, identity, self._task_anchors)

    def _stage(self, path, raw, run, index):
        temp = path.parent / ('.pacttrail-' + run + '-' + str(index) + '.tmp')
        identity = None
        try:
            _check(self._target_anchors)
            with temp.open('xb') as stream:
                identity = _plain(os.fstat(stream.fileno()))
                if stream.write(raw) != len(raw):
                    raise ApplyError('apply_unavailable')
                stream.flush()
                os.fsync(stream.fileno())
            _check(self._target_anchors)
            actual, current, _ = _read(temp, self._target_anchors)
            if current != identity or actual != raw:
                raise ApplyError('apply_unavailable')
            return temp, identity
        except Exception:
            self._remove_owned(temp, identity, self._target_anchors)
            raise

    def _apply_creation(self, run_id, digest, source, payload, text):
        """Persist intent before one exclusive create; never clean up an uncertain target."""
        originals, baselines = {}, []
        for entry in source['files']:
            path = self._target(entry['path'])
            raw, identity, _ = _read(path, self._target_anchors)
            if len(raw) != entry['byte_count'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ApplyError('target_conflict')
            originals[entry['file_id']] = raw.decode('utf-8')
            baselines.append((path, raw, identity))
        validate_draft(text, run_id, source, originals=originals)
        change = payload['changes'][0]
        target = self._target(change['path'])
        try:
            target.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ApplyError('target_conflict')
        value = {'version': 1, 'target_root': str(self.target_root), 'run_id': run_id,
                 'artifact_sha256': digest, 'state': 'unknown', 'error': 'apply_unknown',
                 'files': [{'path': change['path'], 'state': 'pending', 'sha256': change['new_sha256']}]}
        try:
            self._persist(value, first=True)
        except Exception:
            raise ApplyError('apply_unavailable') from None
        attempted = False
        try:
            # Recheck the whole reviewed baseline after persisting intent, before creating anything.
            for path, raw, identity in baselines:
                current, current_identity, _ = _read(path, self._target_anchors)
                if current != raw or current_identity != identity:
                    raise ApplyError('target_conflict')
            _check(self._target_anchors)
            raw = change['new_text'].encode('utf-8')
            attempted = True
            try:
                stream = target.open('xb')
            except FileExistsError:
                attempted = False  # Exclusive open proves that this attempt did not write the target.
                raise ApplyError('target_conflict') from None
            with stream:
                created_identity = _plain(os.fstat(stream.fileno()))
                _check(self._target_anchors)
                if stream.write(raw) != len(raw):
                    raise ApplyError('apply_unknown')
                stream.flush()
                os.fsync(stream.fileno())
                if _plain(os.fstat(stream.fileno())) != created_identity:
                    raise ApplyError('apply_unknown')
                _check(self._target_anchors)
            actual, current_identity, _ = _read(target, self._target_anchors)
            if actual != raw or current_identity != created_identity:
                raise ApplyError('apply_unknown')
            advanced = copy.deepcopy(value)
            advanced['files'][0]['state'] = 'applied'
            advanced.update(state='applied', error=None)
            self._persist(advanced)
            return validate_apply_receipt(advanced)
        except Exception as error:
            if attempted:
                value['files'][0]['state'] = 'unknown'
                value.update(state='unknown', error='apply_unknown')
            else:
                value['files'][0]['state'] = 'not_attempted'
                value.update(state='unavailable',
                             error=error.code if type(error) is ApplyError else 'apply_unavailable')
            try:
                self._persist(value)
            except Exception:
                value['files'][0]['state'] = 'unknown'
                value.update(state='unknown', error='apply_unknown')
            return validate_apply_receipt(value)

    def apply(self, run_id, artifact_sha256, source, changes, *, draft_version=1):
        with self._lock:
            try:
                valid_run_id(run_id)
                artifact_hash(artifact_sha256)
                existing = self.receipt(run_id)
                if existing is not None:
                    if existing['artifact_sha256'] != artifact_sha256:
                        raise ApplyError('target_conflict')
                    return existing
                source = copy.deepcopy(source)
                text = json.dumps({'version': draft_version, 'kind': 'change_draft', 'run_id': run_id,
                    'source_sha256': source['sha256'], 'applied': False,
                    'verification': 'not_run', 'changes': changes}, ensure_ascii=False,
                    sort_keys=True, separators=(',', ':'), allow_nan=False)
                payload = validate_draft(text, run_id, source)
                if hashlib.sha256(text.encode('utf-8')).hexdigest() != artifact_sha256:
                    raise ApplyError('invalid_request')
                if payload['version'] == 2:
                    return self._apply_creation(run_id, artifact_sha256, source, payload, text)
                originals, targets = {}, []
                entries = {item['file_id']: item for item in source['files']}
                for change in payload['changes']:
                    path = self._target(change['path'])
                    raw, identity, _ = _read(path, self._target_anchors)
                    if (hashlib.sha256(raw).hexdigest() != change['base_sha256']
                            or len(raw) != entries[change['file_id']]['byte_count']):
                        raise ApplyError('target_conflict')
                    originals[change['file_id']] = raw.decode('utf-8')
                    targets.append((path, raw, identity))
                validate_draft(text, run_id, source, originals=originals)
            except ApplyError:
                raise
            except FileNotFoundError:
                raise ApplyError('target_conflict') from None
            except Exception:
                raise ApplyError('draft_unavailable') from None
            value = {'version': 1, 'target_root': str(self.target_root), 'run_id': run_id,
                     'artifact_sha256': artifact_sha256, 'state': 'unknown',
                     'files': [{'path': item['path'], 'state': 'not_attempted', 'sha256': item['new_sha256']}
                               for item in payload['changes']], 'error': 'apply_unknown'}
            value['files'][0]['state'] = 'pending'
            try:
                self._persist(value, first=True)
            except Exception:
                raise ApplyError('apply_unavailable') from None
            for index, change in enumerate(payload['changes']):
                path, base, expected = targets[index]
                temp, staged, attempted = None, None, False
                try:
                    raw = change['new_text'].encode('utf-8')
                    temp, staged = self._stage(path, raw, run_id, index)
                    actual, identity, _ = _read(path, self._target_anchors)
                    if actual != base or identity != expected:
                        raise ApplyError('target_conflict')
                    _check(self._target_anchors)
                    if _plain(temp.lstat()) != staged:
                        raise ApplyError('unsafe_path')
                    attempted = True
                    os.replace(temp, path)
                    actual, identity, _ = _read(path, self._target_anchors)
                    if actual != raw or identity != staged:
                        raise ApplyError('apply_unknown')
                    advanced = copy.deepcopy(value)
                    advanced['files'][index]['state'] = 'applied'
                    if index + 1 == len(targets):
                        advanced.update(state='applied', error=None)
                    else:
                        advanced['files'][index + 1]['state'] = 'pending'
                    self._persist(advanced)
                    value = advanced
                except Exception as error:
                    if attempted:
                        value['files'][index]['state'] = 'unknown'
                        value.update(state='unknown', error='apply_unknown')
                    else:
                        value['files'][index]['state'] = 'not_attempted'
                        value.update(state='partial' if index else 'unavailable',
                                     error=error.code if type(error) is ApplyError else 'apply_unavailable')
                    try:
                        self._persist(value)
                    except Exception:
                        # The earlier durable pending intent remains authoritative.
                        value['files'][index]['state'] = 'unknown'
                        value.update(state='unknown', error='apply_unknown')
                    return validate_apply_receipt(value)
                finally:
                    if temp is not None:
                        self._remove_owned(temp, staged, self._target_anchors)
            return validate_apply_receipt(value)
