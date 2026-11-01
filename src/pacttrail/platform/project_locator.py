"""Read-only, bounded discovery under one explicitly bound local project root.

Path searches read directory entries and file metadata only. Text searches read
at most 64 KiB in complete files of at most 8 KiB; results contain no text
snippets. Only explicitly selected snapshot paths enter the existing material
contract. Identity checks detect replacement under trusted local ownership;
they are not an OS sandbox against a hostile local account.
"""
from __future__ import annotations

import base64
import os
import stat
import time

from pacttrail.domain.draft_apply import target_path
from pacttrail.domain.project_source import validate_project_path


MAX_SCANNED_ENTRIES = 512
MAX_DEPTH = 8
MAX_RESULTS = 20
MAX_READ_BYTES = 65536
MAX_FILE_BYTES = 8192
MAX_SNAPSHOT_FILES = 4
MAX_SNAPSHOT_BYTES = 8192
MAX_SECONDS = 2.0
MAX_QUERY_LENGTH = 128
LOCATION_ERRORS = frozenset(('project_location_invalid', 'project_location_unavailable',
                             'project_location_limit'))


class ProjectLocationError(Exception):
    def __init__(self, code):
        self.code = code if type(code) is str and code in LOCATION_ERRORS else 'project_location_unavailable'
        super().__init__('project_location_error: ' + self.code)


def _plain(metadata, *, directory=False):
    if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR if directory else stat.S_ISREG)(metadata.st_mode)
            or not directory and metadata.st_nlink != 1
            or type(metadata.st_ino) is not int or metadata.st_ino <= 0):
        raise ProjectLocationError('project_location_unavailable')
    return metadata.st_dev, metadata.st_ino


def _version(metadata):
    return metadata.st_size, metadata.st_mtime_ns


def _check(anchors):
    for path, identity in anchors.items():
        if _plain(path.lstat(), directory=True) != identity:
            raise ProjectLocationError('project_location_unavailable')


def _directory(path, anchors):
    identity = _plain(path.lstat(), directory=True)
    if identity != anchors.setdefault(path, identity):
        raise ProjectLocationError('project_location_unavailable')


def _read(path, anchors, expected):
    """Open only a validated leaf and verify identity before reading any bytes."""
    _check(anchors)
    before = path.lstat()
    identity, version = _plain(before), _version(before)
    if (identity, version) != expected:
        raise ProjectLocationError('project_location_unavailable')
    if not 0 < before.st_size <= MAX_FILE_BYTES:
        raise ProjectLocationError('project_location_limit')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb', buffering=0) as stream:
        opened = os.fstat(stream.fileno())
        if (_plain(opened), _version(opened)) != expected:
            raise ProjectLocationError('project_location_unavailable')
        # A fixed-size read cannot consume an extra byte after a concurrent grow.
        raw = stream.read(before.st_size)
        after = os.fstat(stream.fileno())
        if (_plain(after), _version(after)) != expected or len(raw) != before.st_size:
            raise ProjectLocationError('project_location_unavailable')
    final = path.lstat()
    if (_plain(final), _version(final)) != expected:
        raise ProjectLocationError('project_location_unavailable')
    _check(anchors)
    return raw


def _query(query, mode):
    if (type(query) is not str or not 1 <= len(query) <= MAX_QUERY_LENGTH or not query.strip()
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in query)
            or type(mode) is not str or mode not in ('path', 'text')):
        raise ProjectLocationError('project_location_invalid')


class ProjectLocator:
    def __init__(self, root):
        try:
            self.root = target_path(os.fspath(root))
        except Exception:
            raise ProjectLocationError('project_location_invalid') from None
        try:
            self._root_anchors = {path: _plain(path.lstat(), directory=True)
                                  for path in reversed((self.root, *self.root.parents))}
            if self.root.resolve(strict=True) != self.root:
                raise ProjectLocationError('project_location_unavailable')
            self.check_root()
        except Exception:
            raise ProjectLocationError('project_location_unavailable') from None
        self.name = self.root.name
        try:
            validate_project_path(self.name + '/selected.txt')
        except Exception:
            self.name = 'bound-project'

    def check_root(self):
        """Check the bound root and ancestors without scanning or reading files."""
        try:
            _check(self._root_anchors)
        except Exception:
            raise ProjectLocationError('project_location_unavailable') from None

    def search(self, query, mode='path'):
        _query(query, mode)
        self.check_root()
        anchors = self._root_anchors.copy()
        result = {'files': [], 'scanned_entries': 0, 'read_bytes': 0,
                  'truncated': False, 'skipped': 0}
        deadline = time.monotonic() + MAX_SECONDS
        directories = [self.root]
        stopped = False
        try:
            while directories and not stopped:
                directory = directories.pop(0)
                _check(anchors)
                _directory(directory, anchors)
                try:
                    entries = os.scandir(directory)
                except OSError:
                    result['skipped'] += 1
                    continue
                with entries:
                    while True:
                        if result['scanned_entries'] >= MAX_SCANNED_ENTRIES or time.monotonic() >= deadline:
                            result['truncated'], stopped = True, True
                            break
                        try:
                            entry = next(entries)
                        except StopIteration:
                            break
                        result['scanned_entries'] += 1
                        path = directory / entry.name
                        relative = path.relative_to(self.root).as_posix()
                        try:
                            metadata = path.lstat()
                            if stat.S_ISDIR(metadata.st_mode):
                                _plain(metadata, directory=True)
                                if len(path.relative_to(self.root).parts) >= MAX_DEPTH:
                                    result['truncated'] = True
                                    result['skipped'] += 1
                                    continue
                                validate_project_path(relative + '/selected.txt')
                                _directory(path, anchors)
                                directories.append(path)
                                continue
                            _plain(metadata)
                            validate_project_path(relative)
                            if metadata.st_size <= 0:
                                result['skipped'] += 1
                                continue
                        except Exception:
                            result['skipped'] += 1
                            continue
                        item = {'path': relative, 'byte_count': metadata.st_size}
                        if mode == 'path':
                            if query.replace('\\', '/').casefold() not in relative.casefold():
                                continue
                        else:
                            if metadata.st_size > MAX_FILE_BYTES:
                                result['truncated'] = True
                                result['skipped'] += 1
                                continue
                            if result['read_bytes'] + metadata.st_size > MAX_READ_BYTES:
                                result['truncated'], stopped = True, True
                                break
                            # Charge attempted bytes as well, so failed/changed files
                            # cannot permit more content reads than the stated bound.
                            result['read_bytes'] += metadata.st_size
                            try:
                                raw = _read(path, anchors, (_plain(metadata), _version(metadata)))
                                text = raw.decode('utf-8')
                            except Exception:
                                _check(anchors)
                                result['skipped'] += 1
                                continue
                            lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
                            line = next((index for index, value in enumerate(lines, 1) if query in value), None)
                            if line is None:
                                continue
                            item['line'] = line
                        result['files'].append(item)
                        if len(result['files']) >= MAX_RESULTS:
                            result['truncated'], stopped = True, True
                            break
                _check(anchors)
            if time.monotonic() >= deadline:
                result['truncated'] = True
            _check(anchors)
            result['files'].sort(key=lambda item: item['path'])
            return result
        except ProjectLocationError:
            raise
        except Exception:
            raise ProjectLocationError('project_location_unavailable') from None

    def snapshot(self, paths):
        """Read exactly 1–4 selected files into the unchanged selected_project contract."""
        if type(paths) not in (list, tuple) or not 1 <= len(paths) <= MAX_SNAPSHOT_FILES:
            raise ProjectLocationError('project_location_invalid')
        try:
            for relative in paths:
                validate_project_path(relative)
            if len({relative.casefold() for relative in paths}) != len(paths):
                raise ValueError()
        except Exception:
            raise ProjectLocationError('project_location_invalid') from None
        self.check_root()
        anchors = self._root_anchors.copy()
        deadline = time.monotonic() + MAX_SECONDS
        selected, files = [], []
        try:
            for relative in paths:
                path = self.root / relative
                for parent in reversed((path.parent, *path.parent.parents)):
                    if parent == self.root or self.root in parent.parents:
                        _directory(parent, anchors)
                metadata = path.lstat()
                expected = _plain(metadata), _version(metadata)
                if not 0 < metadata.st_size <= MAX_FILE_BYTES:
                    raise ProjectLocationError('project_location_limit')
                selected.append((relative, path, expected))
            if sum(expected[1][0] for _, _, expected in selected) > MAX_SNAPSHOT_BYTES:
                raise ProjectLocationError('project_location_limit')
            for relative, path, expected in selected:
                if time.monotonic() >= deadline:
                    raise ProjectLocationError('project_location_limit')
                raw = _read(path, anchors, expected)
                files.append({'path': relative, 'data': base64.b64encode(raw).decode('ascii')})
            from pacttrail.application.chat_project import from_upload
            try:
                project = from_upload(self.name, files)
            except Exception as error:
                code = ('project_location_limit' if str(error) == 'chat_error: material_limit'
                        else 'project_location_unavailable')
                raise ProjectLocationError(code) from None
            for _, path, expected in selected:
                metadata = path.lstat()
                if (_plain(metadata), _version(metadata)) != expected:
                    raise ProjectLocationError('project_location_unavailable')
            _check(anchors)
            if time.monotonic() >= deadline:
                raise ProjectLocationError('project_location_limit')
            return project
        except ProjectLocationError:
            raise
        except Exception:
            raise ProjectLocationError('project_location_unavailable') from None
