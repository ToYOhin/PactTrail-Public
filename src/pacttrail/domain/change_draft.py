"""Bounded, unapplied complete-text proposals with deterministic trusted diffs."""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re

from pacttrail.domain.project_source import validate_project_path, validate_project_source
from pacttrail.domain.tool_task import DecisionError


MAX_NEW_BYTES = 4096
MAX_DRAFT_BYTES = 8192
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_RUN = re.compile(r'[a-f0-9]{32}\Z')


def _require(condition):
    if not condition:
        raise DecisionError()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _source(value):
    chosen = validate_project_source(value)
    _require(chosen['version'] == 1)
    return chosen


def _raw_text(value, limit):
    _require(type(value) is str and 0 < len(value) <= limit and value.strip()
             and not any(ord(c) < 32 and c not in '\r\n\t' or 127 <= ord(c) <= 159
                         or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    raw = value.encode('utf-8')
    _require(len(raw) <= limit)
    return raw


def validate_creation_path(path, source):
    """Bind one absent manifest path without granting any filesystem access."""
    chosen = _source(source)
    validate_project_path(path)
    _require(len(chosen['files']) <= 3 and chosen['byte_count'] < 8192
             and path.casefold() not in {entry['path'].casefold() for entry in chosen['files']})
    return path


def validate_draft_arguments(value, source, *, creation_path=None):
    """Own existing-file replacements or one explicitly bound creation proposal."""
    try:
        chosen = _source(source)
        _require(type(value) is dict and set(value) == {'source_sha256', 'changes'}
                 and value['source_sha256'] == chosen['sha256']
                 and type(value['changes']) is list and 1 <= len(value['changes']) <= 4)
        if creation_path is not None:
            validate_creation_path(creation_path, chosen)
            _require(len(value['changes']) == 1)
            change = value['changes'][0]
            _require(type(change) is dict and set(change) == {'file_id', 'base_sha256', 'new_text'}
                     and change['file_id'] == 'new_file' and change['base_sha256'] == EMPTY_SHA256)
            raw = _raw_text(change['new_text'], MAX_NEW_BYTES)
            _require(_line_count(change['new_text']) <= 256
                     and chosen['byte_count'] + len(raw) <= 8192)
            return copy.deepcopy(value)
        entries = {entry['file_id']: entry for entry in chosen['files']}
        order, used = [], 0
        for change in value['changes']:
            _require(type(change) is dict and set(change) == {'file_id', 'base_sha256', 'new_text'}
                     and type(change['file_id']) is str and change['file_id'] in entries)
            entry = entries[change['file_id']]
            _require(change['base_sha256'] == entry['sha256'])
            raw = _raw_text(change['new_text'], MAX_NEW_BYTES)
            _require(hashlib.sha256(raw).hexdigest() != change['base_sha256'])
            used += len(raw)
            order.append(change['file_id'])
        _require(used <= MAX_NEW_BYTES and len(set(order)) == len(order)
                 and order == [key for key in entries if key in order])
        return copy.deepcopy(value)
    except DecisionError:
        raise
    except (TypeError, ValueError, KeyError, UnicodeError, RecursionError):
        raise DecisionError() from None


def _lines(text):
    # Unified diff lines end in LF; CR remains actual content, preserving CRLF.
    pieces = text.split('\n')
    return [part + '\n' for part in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])


def _line_count(text):
    return len(text.replace('\r\n', '\n').replace('\r', '\n').split('\n'))


def _diff(path, before, after, *, creation=False):
    pieces = difflib.unified_diff(_lines(before), _lines(after),
                                 fromfile='/dev/null' if creation else 'a/' + path,
                                 tofile='b/' + path, lineterm='\n')
    return ''.join(piece if piece.endswith('\n') else piece + '\n\\ No newline at end of file\n'
                   for piece in pieces)


def creation_diff(path, new_text):
    """Generate the whole new-file diff; no source access or write permission."""
    validate_project_path(path)
    _raw_text(new_text, MAX_NEW_BYTES)
    _require(_line_count(new_text) <= 256)
    return _diff(path, '', new_text, creation=True)


def _original(entry, originals, *, strict_text=True):
    before = originals[entry['file_id']]
    _require(type(before) is str)
    raw = _raw_text(before, 8192) if strict_text else before.encode('utf-8')
    _require(len(raw) == entry['byte_count'] and hashlib.sha256(raw).hexdigest() == entry['sha256']
             and _line_count(before) == entry['line_count'])
    return before


def build_draft(run_id, source, args, originals, *, creation_path=None):
    """Generate every diff from verified original bytes, without normalizing content."""
    try:
        _require(type(run_id) is str and _RUN.fullmatch(run_id) is not None)
        chosen = _source(source)
        arguments = validate_draft_arguments(args, chosen, creation_path=creation_path)
        _require(type(originals) is dict)
        entries = {entry['file_id']: entry for entry in chosen['files']}
        changes = []
        if creation_path is not None:
            for entry in entries.values():
                _original(entry, originals, strict_text=False)
            change = arguments['changes'][0]
            changes.append({**change, 'path': creation_path,
                            'new_sha256': hashlib.sha256(change['new_text'].encode('utf-8')).hexdigest(),
                            'diff': creation_diff(creation_path, change['new_text'])})
        else:
            for change in arguments['changes']:
                entry = entries[change['file_id']]
                before = _original(entry, originals)
                changes.append({**change, 'path': entry['path'],
                                'new_sha256': hashlib.sha256(change['new_text'].encode('utf-8')).hexdigest(),
                                'diff': _diff(entry['path'], before, change['new_text'])})
        result = _canonical({'version': 2 if creation_path is not None else 1, 'kind': 'change_draft', 'run_id': run_id,
                             'source_sha256': chosen['sha256'], 'applied': False,
                             'verification': 'not_run', 'changes': changes})
        _require(len(result.encode('utf-8')) <= MAX_DRAFT_BYTES)
        return result
    except DecisionError:
        raise
    except (TypeError, ValueError, KeyError, UnicodeError, RecursionError):
        raise DecisionError() from None


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def validate_draft(text, run_id, source, *, originals=None, creation_path=None):
    """Validate saved facts; optional actual originals prove the whole diff exactly."""
    try:
        chosen = _source(source)
        _require(type(text) is str and 0 < len(text.encode('utf-8')) <= MAX_DRAFT_BYTES
                 and type(run_id) is str and _RUN.fullmatch(run_id) is not None)
        value = json.loads(text, object_pairs_hook=_unique)
        _require(type(value) is dict and set(value) == {
            'version', 'kind', 'run_id', 'source_sha256', 'applied', 'verification', 'changes'}
            and type(value['version']) is int and value['version'] in (1, 2)
            and value['kind'] == 'change_draft' and value['run_id'] == run_id
            and value['source_sha256'] == chosen['sha256'] and value['applied'] is False
            and value['verification'] == 'not_run' and type(value['changes']) is list)
        creating = value['version'] == 2
        if creating:
            _require(len(value['changes']) == 1)
        else:
            _require(creation_path is None)
        arguments, entries = [], {entry['file_id']: entry for entry in chosen['files']}
        for change in value['changes']:
            _require(type(change) is dict and set(change) == {
                'file_id', 'path', 'base_sha256', 'new_sha256', 'new_text', 'diff'}
                and type(change['file_id']) is str
                and (change['file_id'] == 'new_file' if creating else change['file_id'] in entries))
            if creating:
                path = validate_creation_path(change['path'], chosen)
                _require(creation_path is None or path == creation_path)
                creation_path = path
            else:
                path = entries[change['file_id']]['path']
            raw = _raw_text(change['new_text'], MAX_NEW_BYTES)
            _require(change['path'] == path and type(change['new_sha256']) is str
                     and _HASH.fullmatch(change['new_sha256']) is not None
                     and change['new_sha256'] == hashlib.sha256(raw).hexdigest())
            _raw_text(change['diff'], MAX_DRAFT_BYTES)
            _require(change['diff'] == creation_diff(path, change['new_text']) if creating else
                     change['diff'].startswith('--- a/' + path + '\n+++ b/' + path + '\n@@ '))
            arguments.append({key: change[key] for key in ('file_id', 'base_sha256', 'new_text')})
        args = validate_draft_arguments({'source_sha256': value['source_sha256'], 'changes': arguments},
                                        chosen, creation_path=creation_path)
        _require(text == _canonical(value))
        if originals is not None:
            _require(text == build_draft(run_id, chosen, args, originals, creation_path=creation_path))
        return copy.deepcopy(value)
    except DecisionError:
        raise
    except (TypeError, ValueError, KeyError, UnicodeError, RecursionError):
        raise DecisionError() from None
