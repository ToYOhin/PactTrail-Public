"""An explicitly selected artifact snapshot, never a source or write grant."""
from __future__ import annotations

import copy
import hashlib
import re

from pacttrail.domain.runs import PactTrailError


def _require(condition):
    if not condition:
        raise PactTrailError('chat_error: history_corrupt')


def _text(value, maximum, *, empty=False):
    _require(type(value) is str and len(value) <= maximum and (empty or value.strip())
             and not any(ord(c) < 32 and c not in '\r\n\t' or ord(c) == 127
                         or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def validate_reference(value):
    try:
        _require(type(value) is dict and set(value) == {
            'version', 'task_id', 'run_id', 'record_sha256', 'goal', 'kind', 'artifact', 'source', 'state'})
        _require(type(value['version']) is int and value['version'] == 1)
        for key in ('task_id', 'run_id'):
            _require(type(value[key]) is str and re.fullmatch('[a-f0-9]{32}', value[key]) is not None)
        _require(type(value['record_sha256']) is str
                 and re.fullmatch('[a-f0-9]{64}', value['record_sha256']) is not None)
        _text(value['goal'], 4096)
        _require(value['kind'] in ('report', 'change_draft')
                 and value['state'] in ('confirmed', 'review_required'))
        artifact = value['artifact']
        _require(type(artifact) is dict and set(artifact) == {'name', 'sha256', 'bytes', 'text'})
        prefix, suffix = ('draft-', '.json') if value['kind'] == 'change_draft' else ('report-', '.md')
        _require(artifact['name'] == prefix + value['run_id'] + suffix)
        _text(artifact['text'], 8192, empty=True)
        raw = artifact['text'].encode('utf-8')
        _require(type(artifact['bytes']) is int and 0 < len(raw) == artifact['bytes'] <= 8192
                 and type(artifact['sha256']) is str
                 and hashlib.sha256(raw).hexdigest() == artifact['sha256'])
        source = value['source']
        if source is not None:
            _require(type(source) is dict and set(source) == {'kind', 'name', 'sha256'})
            _require(source['kind'] in ('selected_text', 'selected_project')
                     and type(source['sha256']) is str
                     and re.fullmatch('[a-f0-9]{64}', source['sha256']) is not None)
            _text(source['name'], 80)
            _require(not any(ord(c) < 32 for c in source['name']))
        return copy.deepcopy(value)
    except PactTrailError:
        raise
    except Exception:
        raise PactTrailError('chat_error: history_corrupt') from None


def reference_identity(value):
    reference = validate_reference(value)
    return {key: reference[key] for key in ('task_id', 'run_id', 'kind', 'record_sha256')} | {
        'artifact_sha256': reference['artifact']['sha256']}


def validate_identity(value):
    _require(type(value) is dict and set(value) == {
        'task_id', 'run_id', 'kind', 'record_sha256', 'artifact_sha256'})
    _require(value['kind'] in ('report', 'change_draft'))
    for key, size in (('task_id', 32), ('run_id', 32), ('record_sha256', 64), ('artifact_sha256', 64)):
        _require(type(value[key]) is str and re.fullmatch('[a-f0-9]{' + str(size) + '}', value[key]) is not None)
    return copy.deepcopy(value)
