"""Explicit reviewed-draft approval and bounded, independent application facts."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from pacttrail.domain.project_source import validate_project_path


APPLY_ERRORS = frozenset(('invalid_request', 'draft_unavailable', 'target_conflict',
                         'unsafe_path', 'apply_unavailable', 'apply_unknown'))
_RUN = re.compile(r'[a-f0-9]{32}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_DEVICE = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z', re.I)


class ApplyError(Exception):
    def __init__(self, code):
        self.code = code if type(code) is str and code in APPLY_ERRORS else 'apply_unavailable'
        super().__init__('draft_apply_error: ' + self.code)


def _require(value, code='apply_unavailable'):
    if not value:
        raise ApplyError(code)


def run_id(value):
    _require(type(value) is str and _RUN.fullmatch(value) is not None, 'invalid_request')
    return value


def artifact_hash(value):
    _require(type(value) is str and _HASH.fullmatch(value) is not None, 'invalid_request')
    return value


def target_path(value):
    """Validate a path string without opening or resolving the target."""
    try:
        _require(type(value) is str and 1 <= len(value) <= 1024
                 and not value.startswith(('\\\\', '//'))
                 and not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF
                             for c in value), 'unsafe_path')
        path = Path(value)
        _require(path.is_absolute() and path != path.parent and '..' not in path.parts
                 and len(path.parts) <= 64, 'unsafe_path')
        _require(all(':' not in part and not part.endswith(('.', ' ')) and not _DEVICE.fullmatch(part)
                     for part in path.parts[1:]), 'unsafe_path')
        return path
    except (ValueError, TypeError, UnicodeError):
        raise ApplyError('unsafe_path') from None


def canonical_receipt(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def validate_apply_request(value):
    try:
        _require(type(value) is dict and set(value) == {'run_id', 'artifact_sha256', 'confirmed'}
                 and value['confirmed'] is True, 'invalid_request')
        run_id(value['run_id'])
        artifact_hash(value['artifact_sha256'])
        return copy.deepcopy(value)
    except (KeyError, TypeError, ValueError):
        raise ApplyError('invalid_request') from None


def validate_apply_receipt(value):
    """Validate public facts; a pending file always means an unknown outcome."""
    try:
        _require(type(value) is dict and set(value) == {'version', 'target_root', 'run_id',
                 'artifact_sha256', 'state', 'files', 'error'})
        _require(type(value['version']) is int and value['version'] == 1)
        target_path(value['target_root'])
        state, files, error = value['state'], value['files'], value['error']
        _require(type(state) is str and state in ('idle', 'ready', 'applied', 'partial', 'unknown', 'unavailable')
                 and type(files) is list and len(files) <= 4
                 and (error is None or type(error) is str and error in APPLY_ERRORS))
        if value['run_id'] is None:
            _require(value['artifact_sha256'] is None and not files and state in ('idle', 'unavailable'))
        else:
            run_id(value['run_id'])
            artifact_hash(value['artifact_sha256'])
        paths, states = [], []
        for item in files:
            _require(type(item) is dict and set(item) == {'path', 'state', 'sha256'})
            validate_project_path(item['path'])
            artifact_hash(item['sha256'])
            _require(type(item['state']) is str and item['state'] in
                     ('pending', 'applied', 'not_attempted', 'unknown'))
            paths.append(item['path'])
            states.append(item['state'])
        _require(len(set(path.casefold() for path in paths)) == len(paths) and paths == sorted(paths))
        if state == 'idle':
            _require(value['run_id'] is None and not files and error is None)
        elif state == 'ready':
            _require(files and all(item == 'pending' for item in states) and error is None)
        elif state == 'applied':
            _require(files and all(item == 'applied' for item in states) and error is None)
        elif state == 'partial':
            applied = states.count('applied')
            _require(0 < applied < len(states) and states == ['applied'] * applied
                     + ['not_attempted'] * (len(states) - applied) and error is not None)
        elif state == 'unknown':
            applied = states.count('applied')
            _require(error == 'apply_unknown' and len(states) > applied
                     and states[:applied] == ['applied'] * applied
                     and states[applied] in ('pending', 'unknown')
                     and states[applied + 1:] == ['not_attempted'] * (len(states) - applied - 1))
        else:
            _require(error is not None and all(item == 'not_attempted' for item in states))
        _require(len(canonical_receipt(value)) <= 8192)
        return copy.deepcopy(value)
    except ApplyError:
        raise
    except Exception:
        raise ApplyError('apply_unavailable') from None
