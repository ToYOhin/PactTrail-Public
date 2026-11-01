"""An explicit pending question, never execution state or source permission."""
from __future__ import annotations

import copy
import re

from pacttrail.domain.runs import PactTrailError


MAX_SAVED_INPUT_CHARS = 4096
FIELDS = {'id', 'message', 'task_intent', 'creation_path', 'source'}


def source_identity(project):
    return (dict(kind='selected_project', name=project['name'], sha256=project['sha256'])
            if project is not None else None)


def validate_saved_input(value):
    try:
        if (type(value) is not dict or set(value) != FIELDS
                or type(value['id']) is not str or re.fullmatch('[a-f0-9]{32}', value['id']) is None
                or type(value['message']) is not str
                or not 1 <= len(value['message']) <= MAX_SAVED_INPUT_CHARS
                or not value['message'].strip()
                or any(ord(c) < 32 and c not in '\r\n\t' or ord(c) == 127
                       or 0xD800 <= ord(c) <= 0xDFFF for c in value['message'])
                or type(value['task_intent']) is not str
                or value['task_intent'] not in ('answer', 'report', 'change_draft')):
            raise ValueError()
        if value['creation_path'] is not None:
            path = value['creation_path']
            if (value['task_intent'] != 'change_draft' or type(path) is not str or len(path) > 160
                    or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in path)):
                raise ValueError()
        source = value['source']
        if source is not None:
            if (type(source) is not dict or set(source) != {'kind', 'name', 'sha256'}
                    or source['kind'] != 'selected_project' or type(source['name']) is not str
                    or not 1 <= len(source['name']) <= 80 or not source['name'].strip()
                    or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF
                           for c in source['name'])
                    or type(source['sha256']) is not str
                    or re.fullmatch('[a-f0-9]{64}', source['sha256']) is None):
                raise ValueError()
        return copy.deepcopy(value)
    except Exception:
        raise PactTrailError('chat_error: history_corrupt') from None
