"""Small closed decision contract, separate from both text model protocols."""
from __future__ import annotations

import copy
import re

from pacttrail.domain.tool_catalog import tool_names, validate_tool_arguments, validate_tool_reason


MAX_GOAL_CHARS = 2048
MAX_ANSWER_CHARS = 2048
TASK_LIMITS = {'max_decisions': 8, 'max_tools': 7, 'max_read_bytes': 32768,
               'max_total_decisions': 48, 'max_total_tools': 42,
               'max_total_read_bytes': 196608}
FILE_IDS = ('overview.txt', 'plan.txt', 'checks.txt')
SELECTED_FILE_IDS = ('selected.txt',)


class DecisionError(ValueError):
    """Invalid decision; callers publish only a fixed error code."""


def _text(value, limit, *, allow_cr=False):
    return (type(value) is str and 0 < len(value) <= limit and value.strip()
            and not any(ord(c) < 32 and c not in ('\r\n\t' if allow_cr else '\n\t') or ord(c) == 127
                        or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def validate_source_descriptor(value):
    """Own only explicitly selected snapshot identities, never their contents."""
    if type(value) is dict and value.get('kind') == 'selected_project':
        from pacttrail.domain.project_source import validate_project_source
        return validate_project_source(value)
    if (type(value) is not dict or set(value) != {
            'kind', 'file_id', 'name', 'sha256', 'byte_count', 'line_count'}
            or value['kind'] != 'selected_text' or value['file_id'] != 'selected.txt'
            or type(value['name']) is not str or not 1 <= len(value['name']) <= 80
            or not value['name'].lower().endswith('.txt')
            or any(c in '/\\:' or ord(c) < 32 or ord(c) == 127
                   or 0xD800 <= ord(c) <= 0xDFFF for c in value['name'])
            or type(value['sha256']) is not str or re.fullmatch(r'[a-f0-9]{64}', value['sha256']) is None
            or type(value['byte_count']) is not int or not 1 <= value['byte_count'] <= 8192
            or type(value['line_count']) is not int or not 1 <= value['line_count'] <= 256):
        raise DecisionError()
    return copy.deepcopy(value)


def source_file_ids(source):
    if source is None:
        return FILE_IDS
    source = validate_source_descriptor(source)
    if source['kind'] == 'selected_project':
        return tuple(entry['file_id'] for entry in source['files'])
    return SELECTED_FILE_IDS


def validate_decision(value, *, file_ids=FILE_IDS, draft_source=None, creation_path=None, max_summary_chars=1024):
    """Reject malformed envelopes before looking up or invoking any tool."""
    if type(max_summary_chars) is not int or not 1 <= max_summary_chars <= 4096:
        raise DecisionError()
    if draft_source is not None:
        from pacttrail.domain.project_source import validate_project_source
        draft_source = validate_project_source(draft_source)
        if draft_source['version'] != 1 or file_ids != source_file_ids(draft_source):
            raise DecisionError()
    if creation_path is not None:
        from pacttrail.domain.change_draft import validate_creation_path
        validate_creation_path(creation_path, draft_source)
    if (type(file_ids) is not tuple or not (file_ids in (FILE_IDS, SELECTED_FILE_IDS)
            or 1 <= len(file_ids) <= 4 and file_ids == tuple(f'file-{index}' for index in range(1, len(file_ids) + 1)))
            or type(value) is not dict or type(value.get('kind')) is not str):
        raise DecisionError()
    if value['kind'] == 'tools':
        if (set(value) != {'kind', 'calls'} or type(value['calls']) is not list
                or not 2 <= len(value['calls']) <= TASK_LIMITS['max_tools']):
            raise DecisionError()
        for item in value['calls']:
            if type(item) is not dict or item.get('kind') != 'tool':
                raise DecisionError()
            validate_decision(item, file_ids=file_ids, draft_source=draft_source, creation_path=creation_path)
            validate_tool_arguments(item['tool'], item['arguments'], file_ids=file_ids,
                                    draft_source=draft_source, creation_path=creation_path, strict=True)
    elif value['kind'] == 'finish':
        if set(value) != {'kind', 'summary'} or not _text(value['summary'], max_summary_chars):
            raise DecisionError()
    elif value['kind'] == 'tool':
        if (set(value) != {'kind', 'tool', 'arguments', 'reason'}
                or type(value['tool']) is not str
                or value['tool'] not in tool_names(draft=draft_source is not None)
                or type(value['arguments']) is not dict):
            raise DecisionError()
        validate_tool_reason(value['reason'])
        validate_tool_arguments(value['tool'], value['arguments'], file_ids=file_ids,
                                draft_source=draft_source, creation_path=creation_path)
    else:
        raise DecisionError()
    return copy.deepcopy(value)
