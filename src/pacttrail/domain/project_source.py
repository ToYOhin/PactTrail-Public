"""Closed versioned manifest for explicitly selected text project files."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from pacttrail.domain.tool_task import DecisionError


_SUFFIXES = ('.txt', '.md', '.py', '.json')
_EXCLUDED = frozenset(('node_modules', '__pycache__'))
_DEVICE = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3]|CONIN\$|CONOUT\$)(?:\..*)?\Z', re.I)
_METADATA = frozenset(('path', 'sha256', 'byte_count', 'line_count'))


def _component(value):
    if (type(value) is not str or not 1 <= len(value) <= 80 or not value.strip()
            or value.startswith('.') or value.endswith(('.', ' '))
            or value.casefold() in _EXCLUDED or _DEVICE.fullmatch(value)
            or any(c in '/\\:' or ord(c) < 32 or 127 <= ord(c) <= 159
                   or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise DecisionError()
    return value


def validate_project_path(path):
    """Validate a relative display path without normalizing or opening it."""
    if type(path) is not str or not 1 <= len(path) <= 160:
        raise DecisionError()
    parts = path.split('/')
    if len(parts) > 8:
        raise DecisionError()
    for part in parts:
        _component(part)
    if not parts[-1].lower().endswith(_SUFFIXES):
        raise DecisionError()
    return path


def _hash(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _web(value):
    from datetime import datetime
    from pacttrail.domain.web_source import validate_url
    try:
        if (type(value) is not dict or set(value) != {
                'requested_url', 'final_url', 'fetched_at', 'media_type', 'transport'}
                or value['media_type'] not in ('text/plain', 'text/html')
                or value['transport'] not in ('http', 'synthetic')
                or type(value['fetched_at']) is not str
                or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value['fetched_at']) is None):
            raise ValueError()
        datetime.strptime(value['fetched_at'], '%Y-%m-%dT%H:%M:%SZ')
        for key in ('requested_url', 'final_url'):
            if validate_url(value[key])['url'] != value[key]:
                raise ValueError()
    except Exception:
        raise DecisionError() from None


def _metadata(value, version):
    if (type(value) is not dict or set(value) != _METADATA | ({'web'} if version == 2 else set())
            or not _hash(value['sha256'])
            or type(value['byte_count']) is not int or not 1 <= value['byte_count'] <= 8192
            or type(value['line_count']) is not int or not 1 <= value['line_count'] <= 256):
        raise DecisionError()
    validate_project_path(value['path'])
    if version == 2:
        _web(value['web'])
    return copy.deepcopy(value)


def make_project_source(name, metadata_files, *, version=1):
    """Own sorted metadata and bind all fields with one canonical manifest hash."""
    _component(name)
    if (type(version) is not int or version not in (1, 2)
            or type(metadata_files) is not list or not 1 <= len(metadata_files) <= (3 if version == 2 else 4)):
        raise DecisionError()
    entries = sorted((_metadata(value, version) for value in metadata_files), key=lambda value: value['path'])
    if len({value['path'].casefold() for value in entries}) != len(entries):
        raise DecisionError()
    total = sum(value['byte_count'] for value in entries)
    if total > 8192:
        raise DecisionError()
    if version == 2 and (name != 'web-research'
            or [item['path'] for item in entries] != [f'page-{i}.txt' for i in range(1, len(entries) + 1)]
            or len({item['web']['requested_url'] for item in entries}) != len(entries)
            or len({item['web']['transport'] for item in entries}) != 1):
        raise DecisionError()
    value = {'kind': 'selected_project', 'version': version, 'name': name, 'byte_count': total,
             'files': [{'file_id': f'file-{index}', **entry} for index, entry in enumerate(entries, 1)]}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return {**value, 'sha256': hashlib.sha256(raw).hexdigest()}


def validate_project_source(value):
    if (type(value) is not dict or set(value) != {'kind', 'version', 'name', 'sha256', 'byte_count', 'files'}
            or value['kind'] != 'selected_project' or type(value['version']) is not int or value['version'] not in (1, 2)
            or not _hash(value['sha256']) or type(value['byte_count']) is not int
            or type(value['files']) is not list or not 1 <= len(value['files']) <= 4):
        raise DecisionError()
    metadata_files = []
    for entry in value['files']:
        if type(entry) is not dict or set(entry) != _METADATA | {'file_id'} | ({'web'} if value['version'] == 2 else set()):
            raise DecisionError()
        metadata_files.append({key: item for key, item in entry.items() if key != 'file_id'})
    expected = make_project_source(value['name'], metadata_files, version=value['version'])
    if value != expected:
        raise DecisionError()
    return expected
