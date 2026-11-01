"""Fixed synthetic task files under trusted local single-operator ownership.

Identity checks detect replacement; they are not a hostile-filesystem sandbox.
This adapter never adopts existing task roots or resumes an uncertain write.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import threading


MAX_FILE_BYTES = 8192
FILE_IDS = ('overview.txt', 'plan.txt', 'checks.txt')
_SAMPLES = {
    'overview.txt': (
        '# Synthetic project overview\n'
        'This task-only project demonstrates a local inspection and report.\n'
        'If a search has no matches, inspect this overview and describe that result.\n'
        'The project plan is in plan.txt; verification notes are in checks.txt.\n'
    ),
    'plan.txt': (
        '# Synthetic project plan\n'
        'TODO: Review the bounded file access and report workflow.\n'
        'TODO: Compare this plan with checks.txt before writing the report.\n'
        'The checks.txt file records completed checks and remaining work.\n'
    ),
    'checks.txt': (
        '# Synthetic verification notes\n'
        'PASS: Fixed project files can be read and searched locally.\n'
        'PASS: Reports have a fixed name derived from the task run.\n'
        'PENDING: Verify the next implementation against this plan.\n'
    ),
}
_CODES = frozenset(('invalid_argument', 'source_unavailable', 'read_limit',
                    'output_unavailable', 'artifact_conflict'))
_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
_WINDOWS_DEVICE = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z', re.I)


class TaskFileError(Exception):
    def __init__(self, code: str, *, read_bytes: int = 0):
        self.code = code if type(code) is str and code in _CODES else 'output_unavailable'
        self.read_bytes = read_bytes if type(read_bytes) is int and read_bytes >= 0 else 0
        super().__init__('task_file_error: ' + self.code)


@dataclass(frozen=True, slots=True)
class ReportProposal:
    run_id: str
    action_id: str
    content: str
    sha256: str
    byte_count: int
    root_identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class DraftProposal(ReportProposal):
    """A trusted generated draft; it shares the one-artifact write lifecycle."""


def _artifact_name(proposal):
    return ('draft-' + proposal.run_id + '.json' if type(proposal) is DraftProposal
            else 'report-' + proposal.run_id + '.md')


def _identity(metadata):
    return metadata.st_dev, metadata.st_ino


def _plain(metadata, *, directory: bool, code: str):
    if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR if directory else stat.S_ISREG)(metadata.st_mode)
            or (not directory and metadata.st_nlink != 1)):
        raise TaskFileError(code)
    identity = _identity(metadata)
    if any(type(item) is not int or item <= 0 for item in identity):
        raise TaskFileError(code)
    return identity


def _version(metadata):
    # Windows lstat and fstat can expose different ctime semantics (creation vs
    # metadata change). Identity, size and mtime are comparable across both APIs.
    return metadata.st_size, getattr(metadata, 'st_mtime_ns', None)


def _token(value):
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise TaskFileError('invalid_argument')


def _budget(value):
    if type(value) is not int or value < 0:
        raise TaskFileError('invalid_argument')


class TaskFiles:
    def __init__(self, new_absolute_root):
        self._lock = threading.RLock()
        self._proposals: dict[str, ReportProposal] = {}
        self._committed: dict[str, tuple[int, int]] = {}
        self._attempted: set[str] = set()
        self._directories: dict[Path, tuple[int, int]] = {}
        self._inputs: dict[str, tuple[int, int]] = {}
        try:
            raw = os.fspath(new_absolute_root)
            if (type(raw) is not str or not 1 <= len(raw) <= 1024
                    or raw.startswith(('\\\\', '//'))
                    or any(ord(c) < 32 or ord(c) == 127 for c in raw)):
                raise TaskFileError('invalid_argument')
            raw.encode('utf-8')
            root = Path(raw)
            if (not root.is_absolute() or root == root.parent or '..' in root.parts
                    or len(root.parts) > 64
                    or (os.name == 'nt' and any(':' in part or part.endswith(('.', ' '))
                        or _WINDOWS_DEVICE.fullmatch(part) for part in root.parts[1:]))):
                raise TaskFileError('invalid_argument')
            self._root = root
            for parent in reversed(root.parents):
                self._directories[parent] = _plain(parent.lstat(), directory=True, code='invalid_argument')
            # Exclusive reservation: never inspect or adopt existing user contents.
            root.mkdir()
            self._directories[root] = _plain(root.lstat(), directory=True, code='output_unavailable')
            if root.resolve(strict=True) != root:
                raise TaskFileError('invalid_argument')
            for name in ('project', 'reports'):
                child = root / name
                child.mkdir()
                self._directories[child] = _plain(child.lstat(), directory=True, code='output_unavailable')
            for file_id in FILE_IDS:
                path = root / 'project' / file_id
                with path.open('xb') as stream:
                    self._inputs[file_id] = _plain(os.fstat(stream.fileno()), directory=False,
                                                   code='output_unavailable')
                    data = _SAMPLES[file_id].encode('utf-8')
                    if stream.write(data) != len(data):
                        raise TaskFileError('output_unavailable')
            self._check_directories('output_unavailable')
        except TaskFileError:
            raise
        except (TypeError, ValueError, UnicodeError, FileExistsError):
            raise TaskFileError('invalid_argument') from None
        except Exception:
            raise TaskFileError('output_unavailable') from None

    @property
    def root(self):
        return self._root

    def bind_material(self, run_id, material):
        """Copy one validated upload into a new run-owned immutable input scope."""
        from pacttrail.application.chat_material import validate_saved
        _token(run_id)
        try:
            snapshot = validate_saved(material)
        except Exception:
            raise TaskFileError('invalid_argument') from None
        with self._lock:
            try:
                self._check_directories('source_unavailable')
                directory = self._root / ('selected-' + run_id)
                directory.mkdir()
                self._directories[directory] = _plain(directory.lstat(), directory=True,
                                                       code='source_unavailable')
                path = directory / 'selected.txt'
                raw = snapshot['text'].encode('utf-8')
                with path.open('xb') as stream:
                    identity = _plain(os.fstat(stream.fileno()), directory=False,
                                      code='source_unavailable')
                    if stream.write(raw) != len(raw):
                        raise TaskFileError('source_unavailable')
                return SelectedTaskFiles(self, path, identity, snapshot)
            except TaskFileError:
                raise
            except Exception:
                raise TaskFileError('source_unavailable') from None

    def bind_project(self, run_id, project):
        """Persist validated bytes using internal IDs, never user-selected paths."""
        from pacttrail.application.chat_project import validate_saved
        _token(run_id)
        try:
            snapshot = validate_saved(project)
        except Exception:
            raise TaskFileError('invalid_argument') from None
        with self._lock:
            try:
                self._check_directories('source_unavailable')
                directory = self._root / ('project-' + run_id)
                directory.mkdir()
                self._directories[directory] = _plain(directory.lstat(), directory=True,
                                                       code='source_unavailable')
                entries = {}
                for entry in snapshot['files']:
                    path = directory / (entry['file_id'] + '.txt')
                    raw = entry['text'].encode('utf-8')
                    with path.open('xb') as stream:
                        identity = _plain(os.fstat(stream.fileno()), directory=False, code='source_unavailable')
                        if stream.write(raw) != len(raw):
                            raise TaskFileError('source_unavailable')
                    entries[entry['file_id']] = (path, identity)
                return ProjectTaskFiles(self, entries, snapshot)
            except TaskFileError:
                raise
            except Exception:
                raise TaskFileError('source_unavailable') from None

    def _check_directories(self, code):
        for path, expected in self._directories.items():
            if _plain(path.lstat(), directory=True, code=code) != expected:
                raise TaskFileError(code)

    def _read_bytes(self, path, expected, *, code, remaining=None):
        consumed = 0
        try:
            self._check_directories(code)
            before = path.lstat()
            if _plain(before, directory=False, code=code) != expected:
                raise TaskFileError(code)
            if before.st_size > MAX_FILE_BYTES:
                raise TaskFileError('read_limit' if remaining is not None else code)
            if remaining is not None and before.st_size > remaining:
                raise TaskFileError('read_limit')
            with path.open('rb') as stream:
                opened = os.fstat(stream.fileno())
                if (_plain(opened, directory=False, code=code) != expected
                        or _version(opened) != _version(before)):
                    raise TaskFileError(code)
                # Read exactly the verified size, never a sentinel byte beyond a budget.
                raw = stream.read(before.st_size)
                consumed = len(raw)
                after = os.fstat(stream.fileno())
                if (_plain(after, directory=False, code=code) != expected
                        or _version(after) != _version(before) or len(raw) != before.st_size):
                    raise TaskFileError(code)
            final = path.lstat()
            if (_plain(final, directory=False, code=code) != expected
                    or _version(final) != _version(before)):
                raise TaskFileError(code)
            self._check_directories(code)
            text = raw.decode('utf-8')
            if remaining is not None and any(
                    (ord(character) < 32 and character not in '\r\n\t') or ord(character) == 127
                    for character in text):
                raise TaskFileError(code)
            return raw
        except TaskFileError as error:
            raise TaskFileError(error.code, read_bytes=consumed if remaining is not None else 0) from None
        except Exception:
            raise TaskFileError(code, read_bytes=consumed if remaining is not None else 0) from None

    def read(self, file_id, remaining_bytes):
        if type(file_id) is not str or file_id not in FILE_IDS:
            raise TaskFileError('invalid_argument')
        _budget(remaining_bytes)
        with self._lock:
            raw = self._read_bytes(self._root / 'project' / file_id, self._inputs[file_id],
                                   code='source_unavailable', remaining=remaining_bytes)
            return {'status': 'ok', 'file_id': file_id, 'text': raw.decode('utf-8'),
                    'sha256': hashlib.sha256(raw).hexdigest(), 'read_bytes': len(raw)}

    def search(self, query, remaining_bytes):
        if (type(query) is not str or not 1 <= len(query) <= 128 or not query.strip()
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in query)):
            raise TaskFileError('invalid_argument')
        _budget(remaining_bytes)
        with self._lock:
            total, matches = 0, []
            try:
                for file_id in FILE_IDS:
                    result = self.read(file_id, remaining_bytes - total)
                    total += result['read_bytes']
                    for number, line in enumerate(result['text'].splitlines(), 1):
                        if query in line:
                            matches.append({'file_id': file_id, 'line': number, 'text': line})
                return {'status': 'ok', 'matches': matches, 'read_bytes': total}
            except TaskFileError as error:
                raise TaskFileError(error.code, read_bytes=total + error.read_bytes) from None

    def prepare_report(self, run_id, action_id, content):
        _token(run_id)
        _token(action_id)
        try:
            if type(content) is not str or not 1 <= len(content) <= MAX_FILE_BYTES:
                raise TaskFileError('invalid_argument')
            raw = content.encode('utf-8')
            if len(raw) > MAX_FILE_BYTES:
                raise TaskFileError('invalid_argument')
        except UnicodeError:
            raise TaskFileError('invalid_argument') from None
        with self._lock:
            proposal = ReportProposal(run_id, action_id, content, hashlib.sha256(raw).hexdigest(),
                                      len(raw), self._directories[self._root])
            return self._remember_proposal(proposal)

    def _remember_proposal(self, proposal):
        previous = self._proposals.get(proposal.run_id)
        if previous is not None:
            if previous != proposal:
                raise TaskFileError('artifact_conflict')
            return previous
        self._proposals[proposal.run_id] = proposal
        return proposal

    def _prepare_draft(self, run_id, action_id, content):
        raw = content.encode('utf-8')
        with self._lock:
            return self._remember_proposal(DraftProposal(
                run_id, action_id, content, hashlib.sha256(raw).hexdigest(),
                len(raw), self._directories[self._root]))

    def _artifact(self, proposal, identity, code):
        name = _artifact_name(proposal)
        path = self._root / 'reports' / name
        raw = self._read_bytes(path, identity, code=code)
        if raw != proposal.content.encode('utf-8'):
            raise TaskFileError(code)
        return {'name': name, 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                'bytes': len(raw), 'text': raw.decode('utf-8')}

    def save_report(self, proposal):
        if type(proposal) is not ReportProposal:
            raise TaskFileError('invalid_argument')
        return self._save_artifact(proposal)

    def save_draft(self, proposal):
        if type(proposal) is not DraftProposal:
            raise TaskFileError('invalid_argument')
        return self._save_artifact(proposal)

    def _save_artifact(self, proposal):
        with self._lock:
            if (type(proposal) not in (ReportProposal, DraftProposal)
                    or self._proposals.get(proposal.run_id) is not proposal):
                raise TaskFileError('invalid_argument')
            if proposal.run_id in self._committed:
                return self._artifact(proposal, self._committed[proposal.run_id], 'artifact_conflict')
            if proposal.run_id in self._attempted:
                raise TaskFileError('output_unavailable')
            try:
                self._check_directories('output_unavailable')
                path = self._root / 'reports' / _artifact_name(proposal)
                self._attempted.add(proposal.run_id)
                with path.open('xb') as stream:
                    identity = _plain(os.fstat(stream.fileno()), directory=False, code='output_unavailable')
                    raw = proposal.content.encode('utf-8')
                    if stream.write(raw) != len(raw):
                        raise TaskFileError('output_unavailable')
                    stream.flush()
                    os.fsync(stream.fileno())
                artifact = self._artifact(proposal, identity, 'output_unavailable')
                self._committed[proposal.run_id] = identity
                return artifact
            except FileExistsError:
                raise TaskFileError('artifact_conflict') from None
            except TaskFileError:
                raise
            except Exception:
                raise TaskFileError('output_unavailable') from None

    def verify_artifact(self, run_id):
        _token(run_id)
        with self._lock:
            identity = self._committed.get(run_id)
            if identity is None:
                return None
            return self._artifact(self._proposals[run_id], identity, 'artifact_conflict')


class SelectedTaskFiles:
    """Same report store, single hash-bound actual file; no directory inventory."""

    def __init__(self, owner, path, identity, snapshot):
        self._owner, self._path, self._identity = owner, path, identity
        self.source = {'kind': 'selected_text', 'file_id': 'selected.txt',
                       **{key: snapshot[key] for key in ('name', 'sha256', 'byte_count', 'line_count')}}

    def read(self, file_id, remaining_bytes):
        if type(file_id) is not str or file_id != 'selected.txt':
            raise TaskFileError('invalid_argument')
        _budget(remaining_bytes)
        with self._owner._lock:
            raw = self._owner._read_bytes(self._path, self._identity,
                                          code='source_unavailable', remaining=remaining_bytes)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != self.source['sha256'] or len(raw) != self.source['byte_count']:
                raise TaskFileError('source_unavailable', read_bytes=len(raw))
            return {'status': 'ok', 'file_id': file_id, 'text': raw.decode('utf-8'),
                    'sha256': digest, 'read_bytes': len(raw)}

    def search(self, query, remaining_bytes):
        if (type(query) is not str or not 1 <= len(query) <= 128 or not query.strip()
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in query)):
            raise TaskFileError('invalid_argument')
        result = self.read('selected.txt', remaining_bytes)
        matches = [{'file_id': 'selected.txt', 'line': number, 'text': line}
                   for number, line in enumerate(
                       result['text'].replace('\r\n', '\n').replace('\r', '\n').split('\n'), 1)
                   if query in line]
        return {'status': 'ok', 'matches': matches, 'read_bytes': result['read_bytes']}

    def prepare_report(self, *args):
        return self._owner.prepare_report(*args)

    def save_report(self, proposal):
        return self._owner.save_report(proposal)

    def verify_artifact(self, run_id):
        return self._owner.verify_artifact(run_id)


class ProjectTaskFiles:
    """Manifest-scoped read/search using the owner's identity-checked reader."""

    def __init__(self, owner, entries, snapshot):
        self._owner, self._entries = owner, entries
        self.source = {**snapshot, 'files': [{k: v for k, v in entry.items() if k != 'text'}
                                            for entry in snapshot['files']]}

    def read(self, file_id, remaining_bytes):
        if type(file_id) is not str or file_id not in self._entries:
            raise TaskFileError('invalid_argument')
        _budget(remaining_bytes)
        with self._owner._lock:
            path, identity = self._entries[file_id]
            raw = self._owner._read_bytes(path, identity, code='source_unavailable', remaining=remaining_bytes)
            entry = next(entry for entry in self.source['files'] if entry['file_id'] == file_id)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != entry['sha256'] or len(raw) != entry['byte_count']:
                raise TaskFileError('source_unavailable', read_bytes=len(raw))
            return {'status': 'ok', 'file_id': file_id, 'text': raw.decode('utf-8'),
                    'sha256': digest, 'read_bytes': len(raw)}

    def search(self, query, remaining_bytes):
        if (type(query) is not str or not 1 <= len(query) <= 128 or not query.strip()
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in query)):
            raise TaskFileError('invalid_argument')
        _budget(remaining_bytes)
        matches, used = [], 0
        with self._owner._lock:
            try:
                for file_id in self._entries:
                    result = self.read(file_id, remaining_bytes - used)
                    used += result['read_bytes']
                    matches.extend({'file_id': file_id, 'line': number, 'text': line}
                                   for number, line in enumerate(result['text'].replace('\r\n', '\n')
                                                               .replace('\r', '\n').split('\n'), 1)
                                   if query in line)
            except TaskFileError as error:
                raise TaskFileError(error.code, read_bytes=used + error.read_bytes) from None
        return {'status': 'ok', 'matches': matches, 'read_bytes': used}

    def prepare_draft(self, run_id, action_id, args, remaining_bytes, *, creation_path=None):
        from pacttrail.domain.change_draft import build_draft, validate_draft_arguments
        from pacttrail.domain.tool_task import DecisionError
        _token(run_id)
        _token(action_id)
        _budget(remaining_bytes)
        try:
            arguments = validate_draft_arguments(args, self.source, creation_path=creation_path)
        except DecisionError:
            raise TaskFileError('invalid_argument') from None
        originals, used = {}, 0
        with self._owner._lock:
            try:
                reads = self.source['files'] if creation_path is not None else arguments['changes']
                for change in reads:
                    actual = self.read(change['file_id'], remaining_bytes - used)
                    used += actual['read_bytes']
                    originals[change['file_id']] = actual['text']
                content = build_draft(run_id, self.source, arguments, originals, creation_path=creation_path)
                proposal = self._owner._prepare_draft(run_id, action_id, content)
                return proposal, used
            except TaskFileError as error:
                raise TaskFileError(error.code, read_bytes=used + error.read_bytes) from None
            except DecisionError:
                raise TaskFileError('invalid_argument', read_bytes=used) from None
