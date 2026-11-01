"""Atomic local task facts and read-only, non-executing recovery projections."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading

from pacttrail.domain.task_record import (
    MAX_RECORDS, MAX_RECORD_BYTES, TaskRecordError, canonical_json, identifier,
    record_digest, validate_task_record,
)
from pacttrail.domain.tool_task import FILE_IDS


_ENVELOPE_LIMIT = MAX_RECORD_BYTES + 4096


def _plain(metadata, *, directory=False):
    if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR if directory else stat.S_ISREG)(metadata.st_mode)
            or not directory and metadata.st_nlink != 1
            or type(metadata.st_ino) is not int or metadata.st_ino <= 0):
        raise TaskRecordError('record_unavailable')
    return metadata.st_dev, metadata.st_ino


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TaskRecordError('record_corrupt')
        result[key] = value
    return result


def _constant(_):
    raise TaskRecordError('record_corrupt')


def _failure(error):
    return error if type(error) is TaskRecordError else TaskRecordError('record_unavailable')


class TaskRecordStore:
    """A new-root writer or existing-root read-only reader, never a task executor.

    Content identities survive an offline whole-root backup. Runtime inode checks
    reject path swaps and links during this object's lifetime; they are not an OS
    sandbox against a hostile local account. No read operation repairs files.
    """

    def __init__(self, root, *, create=False):
        self._lock = threading.RLock()
        self._directories = {}
        self._writer = create is True
        try:
            raw = os.fspath(root)
            if (type(create) is not bool or type(raw) is not str or not 1 <= len(raw) <= 1024
                    or raw.startswith(('\\\\', '//')) or any(ord(c) < 32 or ord(c) == 127 for c in raw)):
                raise TaskRecordError('record_unavailable')
            raw.encode('utf-8')
            self.root = Path(raw)
            if (not self.root.is_absolute() or self.root == self.root.parent or '..' in self.root.parts
                    or len(self.root.parts) > 64
                    or os.name == 'nt' and any(':' in part or part.endswith(('.', ' '))
                                               for part in self.root.parts[1:])):
                raise TaskRecordError('record_unavailable')
            for parent in reversed((self.root, *self.root.parents)):
                self._directory(parent)
            self._records = self.root / 'records'
            if create:
                self._records.mkdir()
            self._directory(self._records)
            if self.root.resolve() != self.root:
                raise TaskRecordError('record_unavailable')
            self._anchors = tuple(self._directories)
            # Bind the generated sample scope when the new writer is created.
            # Later source damage is a tool/integrity fact, not a reason to lose
            # the run's record or its already acknowledged report receipt.
            self._initial_sources = None
            if create:
                self._initial_sources = []
                for file_id in FILE_IDS:
                    data = self._read(self.root / 'project' / file_id, 8192)
                    self._initial_sources.append({'file_id': file_id,
                                                  'sha256': hashlib.sha256(data).hexdigest(),
                                                  'bytes': len(data)})
        except Exception as error:
            raise _failure(error) from None

    def _directory(self, path):
        identity = _plain(path.lstat(), directory=True)
        expected = self._directories.setdefault(path, identity)
        if identity != expected:
            raise TaskRecordError('record_unavailable')

    def _check_directories(self):
        for path in self._anchors:
            expected = self._directories[path]
            if _plain(path.lstat(), directory=True) != expected:
                raise TaskRecordError('record_unavailable')

    def _read(self, path, limit):
        self._check_directories()
        self._directory(path.parent)
        before = path.lstat()
        expected = _plain(before)
        if not 0 < before.st_size <= limit:
            raise TaskRecordError('record_corrupt')
        version = before.st_size, before.st_mtime_ns
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if _plain(opened) != expected or (opened.st_size, opened.st_mtime_ns) != version:
                raise TaskRecordError('record_unavailable')
            raw = stream.read(before.st_size)
            after = os.fstat(stream.fileno())
            if (_plain(after) != expected or (after.st_size, after.st_mtime_ns) != version
                    or len(raw) != before.st_size):
                raise TaskRecordError('record_unavailable')
        final = path.lstat()
        if _plain(final) != expected or (final.st_size, final.st_mtime_ns) != version:
            raise TaskRecordError('record_unavailable')
        self._directory(path.parent)
        self._check_directories()
        return raw

    def _inventory(self):
        self._check_directories()
        names = set()
        with os.scandir(self._records) as entries:
            for count, entry in enumerate(entries, 1):
                if count > MAX_RECORDS * 2:
                    raise TaskRecordError('record_corrupt')
                if entry.name.endswith('.tmp'):
                    raise TaskRecordError('record_partial')
                if re.fullmatch('[a-f0-9]{32}\\.(json|bak)', entry.name) is None:
                    raise TaskRecordError('record_corrupt')
                _plain((self._records / entry.name).lstat())
                names.add(entry.name)
        runs = sorted(name[:-5] for name in names if name.endswith('.json'))
        if len(runs) > MAX_RECORDS or any(name[:-4] not in runs for name in names if name.endswith('.bak')):
            raise TaskRecordError('record_corrupt')
        return runs, names

    @staticmethod
    def _source_paths(record):
        source, run_id = record['task']['source'], record['run_id']
        if source is None:
            return [(file_id, Path('project') / file_id, None) for file_id in FILE_IDS]
        if source['kind'] == 'selected_text':
            return [('selected.txt', Path('selected-' + run_id) / 'selected.txt', source)]
        return [(entry['file_id'], Path('project-' + run_id) / (entry['file_id'] + '.txt'), entry)
                for entry in source['files']]

    def _sources(self, record):
        if record['task']['source'] is None:
            return copy.deepcopy(self._initial_sources)
        result = []
        for file_id, relative, entry in self._source_paths(record):
            result.append({'file_id': file_id, 'sha256': entry['sha256'], 'bytes': entry['byte_count']})
        return result

    def _decode(self, raw, run_id):
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
            if type(value) is not dict or set(value) != {'version', 'record', 'sources', 'sha256'}:
                raise TaskRecordError('record_corrupt')
            if type(value['version']) is not int:
                raise TaskRecordError('record_corrupt')
            if value['version'] != 1:
                raise TaskRecordError('record_version')
            record = validate_task_record(value['record'])
            if record['run_id'] != run_id:
                raise TaskRecordError('record_conflict')
            sources = value['sources']
            paths = self._source_paths(record)
            if type(sources) is not list or len(sources) != len(paths):
                raise TaskRecordError('record_corrupt')
            for saved, (file_id, _, entry) in zip(sources, paths):
                if (type(saved) is not dict or set(saved) != {'file_id', 'sha256', 'bytes'}
                        or saved['file_id'] != file_id or type(saved['sha256']) is not str
                        or re.fullmatch('[a-f0-9]{64}', saved['sha256']) is None
                        or type(saved['bytes']) is not int or not 1 <= saved['bytes'] <= 8192
                        or entry and (saved['sha256'] != entry['sha256'] or saved['bytes'] != entry['byte_count'])):
                    raise TaskRecordError('record_corrupt')
            payload = {key: value[key] for key in ('version', 'record', 'sources')}
            if value['sha256'] != record_digest(payload):
                raise TaskRecordError('record_corrupt')
            return value
        except TaskRecordError:
            raise
        except (UnicodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise TaskRecordError('record_corrupt') from None

    def _load(self, run_id, names):
        path = self._records / (run_id + '.json')
        if path.name not in names:
            raise TaskRecordError('record_missing')
        raw = self._read(path, _ENVELOPE_LIMIT)
        envelope = self._decode(raw, run_id)
        backup = run_id + '.bak'
        if backup in names:
            old = self._decode(self._read(self._records / backup, _ENVELOPE_LIMIT), run_id)
            if old['record']['revision'] != envelope['record']['revision'] - 1:
                raise TaskRecordError('record_conflict')
        return envelope, raw

    def _integrity(self, envelope):
        record = envelope['record']
        source_changed = False
        for saved, (_, relative, _) in zip(envelope['sources'], self._source_paths(record)):
            try:
                raw = self._read(self.root / relative, 8192)
                source_changed |= len(raw) != saved['bytes'] or hashlib.sha256(raw).hexdigest() != saved['sha256']
            except Exception:
                source_changed = True
        artifact_changed = False
        if record['task']['artifact'] is not None:
            try:
                self._report_bytes(record, check_access=False, verify_sources=not source_changed)
            except Exception:
                artifact_changed = True
        return ('both_changed' if source_changed and artifact_changed else 'source_changed' if source_changed
                else 'artifact_changed' if artifact_changed else 'verified')

    @staticmethod
    def _recovery(record):
        pending = record['pending']
        if pending is not None and pending['kind'] == 'tool' and pending['tool'] in ('save_report', 'save_draft'):
            return 'write_unknown'
        return 'interrupted' if record['status'] == 'running' or pending is not None else 'terminal'

    def read_record(self, run_id):
        with self._lock:
            try:
                identifier(run_id)
                _, names = self._inventory()
                envelope, _ = self._load(run_id, names)
                record = copy.deepcopy(envelope['record'])
                if record['pending'] is not None and record['pending']['kind'] in ('model', 'search'):
                    record['cost']['cost_known'] = False
                    if record['pending']['kind'] == 'search':
                        record['cost'].update(stopped=True, stop_reason='usage_unavailable')
                return {'record': record, 'recovery': self._recovery(record),
                        'integrity': self._integrity(envelope), 'report': None}
            except Exception as error:
                raise _failure(error) from None

    def list_records(self):
        with self._lock:
            try:
                runs, _ = self._inventory()
                result = []
                for run_id in runs:
                    try:
                        detail = self.read_record(run_id)
                        record = detail['record']
                        result.append({**{key: record[key] for key in ('run_id', 'revision', 'created_at', 'updated_at', 'goal', 'status')},
                                       'recovery': detail['recovery'], 'integrity': detail['integrity'],
                                       'has_report': record['task']['artifact'] is not None and detail['recovery'] != 'write_unknown',
                                       'error': None})
                    except TaskRecordError as error:
                        result.append({'run_id': run_id, 'revision': 0, 'created_at': 0, 'updated_at': 0,
                                       'goal': '', 'status': 'unavailable', 'recovery': 'unavailable',
                                       'integrity': 'unavailable', 'has_report': False, 'error': error.code})
                return sorted(result, key=lambda item: (-item['created_at'], item['run_id']))
            except Exception as error:
                raise _failure(error) from None

    def _report_bytes(self, record, *, check_access=True, verify_sources=True):
        receipt = record['task']['artifact']
        if receipt is None or check_access and self._recovery(record) == 'write_unknown':
            raise TaskRecordError('record_report_unavailable')
        try:
            raw = self._read(self.root / 'reports' / receipt['name'], 8192)
            if len(raw) != receipt['bytes'] or hashlib.sha256(raw).hexdigest() != receipt['sha256']:
                raise TaskRecordError('record_artifact_changed')
            text = raw.decode('utf-8')
            if record['version'] in (4, 6):
                from pacttrail.domain.change_draft import validate_draft
                originals = {} if verify_sources else None
                if verify_sources:
                    for file_id, relative, entry in self._source_paths(record):
                        original = self._read(self.root / relative, 8192)
                        if len(original) != entry['byte_count'] or hashlib.sha256(original).hexdigest() != entry['sha256']:
                            raise TaskRecordError('record_artifact_changed')
                        originals[file_id] = original.decode('utf-8')
                validate_draft(text, record['run_id'], record['task']['source'], originals=originals)
            return raw
        except Exception:
            raise TaskRecordError('record_artifact_changed') from None

    def report(self, run_id, *, historical_reference=False):
        with self._lock:
            try:
                if type(historical_reference) is not bool:
                    raise TaskRecordError('record_report_unavailable')
                detail = self.read_record(run_id)
                record = detail['record']
                if historical_reference and detail['integrity'] not in ('verified', 'source_changed'):
                    raise TaskRecordError('record_artifact_changed')
                # A historical artifact may remain a verified reference after
                # its copied source changes. It grants no current apply scope.
                raw = self._report_bytes(record, verify_sources=not historical_reference)
                if historical_reference and self.read_record(run_id)['record'] != record:
                    raise TaskRecordError('record_conflict')
                return {**record['task']['artifact'], 'text': raw.decode('utf-8')}
            except Exception as error:
                raise _failure(error) from None

    def artifact_missing(self, run_id):
        """Distinguish a missing acknowledged body without reading changed bytes."""
        with self._lock:
            detail = self.read_record(run_id)
            receipt = detail['record']['task']['artifact']
            if receipt is None:
                return True
            self._check_directories()
            try:
                path = self.root / 'reports' / receipt['name']
                self._directory(path.parent)
                _plain(path.lstat())
                self._check_directories()
                return False
            except FileNotFoundError:
                return True

    def source_evidence(self, run_id):
        """Reopen only recorded observations; never reread a live project root.

        Successful reads identify a complete snapshot even in legacy records
        whose display preview was shortened. Search previews prove only their
        retained hit lines, not that the full file was supplied to the model.
        """
        with self._lock:
            try:
                identifier(run_id)
                _, names = self._inventory()
                envelope, _ = self._load(run_id, names)
                record = copy.deepcopy(envelope['record'])
                descriptor = record['task']['source']
                signatures, sources = [], []
                for saved, (file_id, relative, entry) in zip(envelope['sources'], self._source_paths(record)):
                    reads, hits = [], []
                    for step in record['task']['steps']:
                        result = step['result']
                        if step['kind'] != 'tool' or result.get('status') != 'ok':
                            continue
                        if step['tool'] == 'read' and result['file_id'] == file_id:
                            reads.append((step['index'], result))
                        elif step['tool'] == 'search':
                            hits.extend(dict(step=step['index'], line=hit['line'], text=hit['text'], complete=False)
                                        for hit in result['matches'] if hit['file_id'] == file_id)
                    hit_steps = {hit['step'] for hit in hits}
                    distinct, retained = set(), []
                    for hit in hits:
                        key = hit['line'], hit['text']
                        if key not in distinct:
                            distinct.add(key)
                            retained.append(hit)
                    hits = retained
                    observation = 'read' if reads else 'search' if hits else 'none'
                    name = (file_id if entry is None else descriptor['name']
                            if descriptor['kind'] == 'selected_text' else entry['path'])
                    source = dict(file_id=file_id, name=name, sha256=saved['sha256'], bytes=saved['bytes'],
                        line_count=None if entry is None else entry['line_count'],
                        web=copy.deepcopy(entry.get('web')) if entry else None,
                        state='unobserved', observation=observation,
                        steps=sorted({index for index, _ in reads} | hit_steps),
                        text=None, preview=reads[0][1]['text'] if reads else None, matches=hits)
                    sources.append(source)
                    if observation == 'none':
                        continue
                    path = self.root / relative
                    try:
                        metadata = path.lstat()
                        signature = (_plain(metadata), metadata.st_size, metadata.st_mtime_ns)
                        raw = self._read(path, 8192)
                        if len(raw) != saved['bytes'] or hashlib.sha256(raw).hexdigest() != saved['sha256']:
                            source['state'] = 'changed'
                            continue
                        text = raw.decode('utf-8')
                        if any((result['sha256'] != saved['sha256'] or result['read_bytes'] != saved['bytes']
                                or result['text'] != raw[:512].decode('utf-8', errors='ignore'))
                               for _, result in reads):
                            source['state'] = 'changed'
                            continue
                        # Synthetic search used splitlines; selected snapshots
                        # have always used normalized LF positions.
                        lines = (text.splitlines() if descriptor is None else
                                 text.replace('\r\n', '\n').replace('\r', '\n').split('\n'))
                        if any(hit['line'] > len(lines)
                               or lines[hit['line'] - 1].encode('utf-8')[:128].decode('utf-8', errors='ignore') != hit['text']
                               for hit in hits):
                            source['state'] = 'changed'
                            continue
                        source.update(state='verified', line_count=len(
                            text.replace('\r\n', '\n').replace('\r', '\n').split('\n')),
                            text=text if reads else None,
                            matches=[dict(hit, text=lines[hit['line'] - 1], complete=True) for hit in hits])
                        signatures.append((path, signature))
                    except FileNotFoundError:
                        source['state'] = 'missing'
                    except Exception:
                        source['state'] = 'unavailable'
                report, artifact_state = None, 'none'
                if self._recovery(record) == 'write_unknown':
                    artifact_state = 'unknown'
                elif record['task']['artifact'] is not None:
                    report_path = self.root / 'reports' / record['task']['artifact']['name']
                    try:
                        metadata = report_path.lstat()
                        signature = (_plain(metadata), metadata.st_size, metadata.st_mtime_ns)
                        raw = self._report_bytes(record, verify_sources=False)
                        report = dict(record['task']['artifact'], text=raw.decode('utf-8'))
                        artifact_state = 'verified'
                        signatures.append((report_path, signature))
                    except FileNotFoundError:
                        artifact_state = 'missing'
                    except TaskRecordError:
                        artifact_state = 'changed'
                    except Exception:
                        artifact_state = 'unavailable'
                for path, expected in signatures:
                    metadata = path.lstat()
                    if (_plain(metadata), metadata.st_size, metadata.st_mtime_ns) != expected:
                        raise TaskRecordError('record_conflict')
                _, final_names = self._inventory()
                final, _ = self._load(run_id, final_names)
                if final['sha256'] != envelope['sha256']:
                    raise TaskRecordError('record_conflict')
                return dict(record=record, sources=sources, report=report, artifact_state=artifact_state)
            except Exception as error:
                raise _failure(error) from None

    def prepare_followup(self, run_id, *, round_count=None):
        """Read a complete parent's exact bytes without granting execution authority."""
        from pacttrail.domain.project_source import make_project_source
        from pacttrail.domain.task_followup import context_rounds, snapshot_source, validate_bundle
        with self._lock:
            try:
                if round_count is not None and (type(round_count) is not int or not 1 <= round_count <= 3):
                    raise TaskRecordError('record_conflict')
                identifier(run_id)
                _, names = self._inventory()
                envelope, _ = self._load(run_id, names)
                record = envelope['record']
                if (record['version'] in (4, 6) or record['status'] != 'complete' or record['error'] is not None
                        or record['pending'] is not None or record['task']['artifact'] is None):
                    raise TaskRecordError('record_report_unavailable')
                history = []
                if round_count is not None:
                    available = context_rounds(record['followup_context']) if record['version'] == 3 else []
                    if round_count > len(available) + 1:
                        raise TaskRecordError('record_report_unavailable')
                    if round_count > 1:
                        history = available[-(round_count - 1):]
                contents, signatures = {}, []
                for saved, (file_id, relative, _) in zip(envelope['sources'], self._source_paths(record)):
                    path = self.root / relative
                    try:
                        before = path.lstat()
                        signature = (_plain(before), before.st_size, before.st_mtime_ns)
                        raw = self._read(path, 8192)
                        if len(raw) != saved['bytes'] or hashlib.sha256(raw).hexdigest() != saved['sha256']:
                            raise TaskRecordError('record_unavailable')
                        contents[file_id] = raw.decode('utf-8')
                        signatures.append((path, signature, 'record_unavailable'))
                    except Exception:
                        raise TaskRecordError('record_unavailable') from None
                source = record['task']['source']
                if source is None:
                    entries = [{'path': saved['file_id'], 'sha256': saved['sha256'],
                                'byte_count': saved['bytes'], 'line_count': len(
                                    contents[saved['file_id']].replace('\r\n', '\n').replace('\r', '\n').split('\n'))}
                               for saved in envelope['sources']]
                    value = make_project_source('synthetic-project', entries)
                    value['files'] = [{**entry, 'text': contents[entry['path']]} for entry in value['files']]
                    snapshot = {'kind': 'project', 'value': value}
                elif source['kind'] == 'selected_text':
                    snapshot = {'kind': 'material', 'value': {
                        **{key: source[key] for key in ('name', 'sha256', 'byte_count', 'line_count')},
                        'text': contents['selected.txt']}}
                else:
                    value = copy.deepcopy(source)
                    value['files'] = [{**entry, 'text': contents[entry['file_id']]} for entry in value['files']]
                    snapshot = {'kind': 'project', 'value': value}
                report_path = self.root / 'reports' / ('report-' + run_id + '.md')
                try:
                    before = report_path.lstat()
                    signature = (_plain(before), before.st_size, before.st_mtime_ns)
                    report_bytes = self._report_bytes(record)
                    signatures.append((report_path, signature, 'record_artifact_changed'))
                except Exception:
                    raise TaskRecordError('record_artifact_changed') from None
                parent = {**{key: copy.deepcopy(record[key]) for key in ('run_id', 'revision', 'goal', 'cost')},
                          'checkpoint_sha256': envelope['sha256'],
                          'artifact': copy.deepcopy(record['task']['artifact'])}
                bundle = {'version': 1, 'parent': parent, 'snapshot': snapshot,
                          'report': {**parent['artifact'], 'text': report_bytes.decode('utf-8')}}
                if round_count is not None:
                    if record['version'] == 3 and snapshot_source(snapshot) != record['followup_context']['source']:
                        raise TaskRecordError('record_corrupt')
                    bundle.update(version=2, history=history)
                bundle['sha256'] = record_digest(bundle)
                bundle = validate_bundle(bundle)
                for path, expected, code in signatures:
                    try:
                        current = path.lstat()
                        if (_plain(current), current.st_size, current.st_mtime_ns) != expected:
                            raise TaskRecordError(code)
                    except Exception:
                        raise TaskRecordError(code) from None
                _, final_names = self._inventory()
                final, _ = self._load(run_id, final_names)
                if final['sha256'] != envelope['sha256']:
                    raise TaskRecordError('record_conflict')
                return bundle
            except Exception as error:
                raise _failure(error) from None

    def _stage(self, path, raw):
        self._check_directories()
        with path.open('xb') as stream:
            identity = _plain(os.fstat(stream.fileno()))
            if stream.write(raw) != len(raw):
                raise TaskRecordError('record_unavailable')
            stream.flush()
            os.fsync(stream.fileno())
            if _plain(os.fstat(stream.fileno())) != identity:
                raise TaskRecordError('record_unavailable')
        if _plain(path.lstat()) != identity:
            raise TaskRecordError('record_unavailable')
        self._check_directories()

    def write(self, value):
        with self._lock:
            try:
                if not self._writer:
                    raise TaskRecordError('record_read_only')
                record = validate_task_record(value)
                runs, names = self._inventory()
                run_id = record['run_id']
                old, previous = self._load(run_id, names) if run_id in runs else (None, None)
                if old is not None:
                    before = old['record']
                    if record == before:
                        return copy.deepcopy(record)
                    binding_source = (before['task']['source'] is None and record['task']['source'] is not None
                                      and not before['task']['steps'] and before['pending'] is None
                                      and before['task']['budget']['decisions'] == before['task']['budget']['tools'] == 0)
                    if (record['revision'] != before['revision'] + 1
                            or record['version'] != before['version']
                            or record.get('parent') != before.get('parent')
                            or record.get('followup_context') != before.get('followup_context')
                            or ('artifact_reference' in record) != ('artifact_reference' in before)
                            or record.get('artifact_reference') != before.get('artifact_reference')
                            or any(record[key] != before[key] for key in ('created_at', 'goal'))
                            or record['updated_at'] < before['updated_at']
                            or record['task']['source'] != before['task']['source'] and not binding_source
                            or record['cost']['mode'] != before['cost']['mode']
                            or before['status'] != 'running' and record['status'] == 'running'):
                        raise TaskRecordError('record_conflict')
                    sources = self._sources(record) if binding_source else old['sources']
                else:
                    if record['revision'] != 1 or len(runs) >= MAX_RECORDS:
                        raise TaskRecordError('record_conflict')
                    sources = self._sources(record)
                envelope = {'version': 1, 'record': record, 'sources': sources}
                envelope['sha256'] = record_digest(envelope)
                raw = canonical_json(envelope)
                if len(raw) > _ENVELOPE_LIMIT:
                    raise TaskRecordError('record_corrupt')
                stage = self._records / (run_id + '.tmp')
                self._stage(stage, raw)
                if previous is not None:
                    backup_stage = self._records / (run_id + '.bak.tmp')
                    self._stage(backup_stage, previous)
                    self._check_directories()
                    os.replace(backup_stage, self._records / (run_id + '.bak'))
                self._check_directories()
                os.replace(stage, self._records / (run_id + '.json'))
                if self._read(self._records / (run_id + '.json'), _ENVELOPE_LIMIT) != raw:
                    raise TaskRecordError('record_unavailable')
                return copy.deepcopy(record)
            except Exception as error:
                raise _failure(error) from None
