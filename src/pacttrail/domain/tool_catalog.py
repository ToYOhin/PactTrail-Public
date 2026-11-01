"""The four existing local tools: closed definitions, schemas and arguments.

This catalog grants no execution authority. The application still owns source
binding, budgets, cancellation, file preparation and committed-write receipts.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass


MAX_REASON_CHARS = 256


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    argument_fields: tuple[str, ...]
    handler: str
    prepare_handler: str | None
    descriptions: tuple[str, str, str]
    max_chars: int | None = None
    max_bytes: int | None = None


TOOL_DEFINITIONS = (
    ToolDefinition('search', ('query',), 'search', None, (
        'Search fixed synthetic project text literally.',
        'Search only the selected UTF-8 snapshot literally.',
        'Search only the selected project manifest files literally.'), 80),
    ToolDefinition('read', ('file_id',), 'read', None, (
        'Read one fixed synthetic project text file.',
        'Read only the selected UTF-8 snapshot.',
        'Read one selected project file by its manifest file ID.'), 80),
    ToolDefinition('save_report', ('content',), 'save_report', 'prepare_report', (
        'Save one bounded report under the current run identity.',) * 3, 6000, 8192),
    ToolDefinition('save_draft', ('source_sha256', 'changes'), 'save_draft', 'prepare_draft', (
        'Save one unapplied draft and trusted diff in the current task root.',) * 3),
)


def _require(condition):
    if not condition:
        from pacttrail.domain.tool_task import DecisionError
        raise DecisionError()


def tool_definition(name):
    """Return only a fixed definition; names cannot register new handlers."""
    _require(type(name) is str)
    for definition in TOOL_DEFINITIONS:
        if definition.name == name:
            return definition
    _require(False)


def tool_names(*, draft=False):
    excluded = 'save_report' if draft else 'save_draft'
    return tuple(definition.name for definition in TOOL_DEFINITIONS if definition.name != excluded)


def _text(value, limit, *, allow_cr=False):
    return (type(value) is str and 0 < len(value) <= limit and value.strip()
            and not any(ord(c) < 32 and c not in ('\r\n\t' if allow_cr else '\n\t') or ord(c) == 127
                        or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def validate_tool_reason(value):
    _require(_text(value, MAX_REASON_CHARS))


def validate_tool_arguments(name, arguments, *, file_ids, draft_source=None, creation_path=None, strict=False):
    """Validate the existing contract, retaining the synthetic single-call path.

    Batch/provider calls use strict=True: reads must be in scope and searches
    must be literal single-line queries. A single synthetic sample read retains
    its old file-adapter error observation for unknown IDs.
    """
    from pacttrail.domain.tool_task import FILE_IDS
    _require(name in tool_names(draft=draft_source is not None))
    definition = tool_definition(name)
    _require(type(arguments) is dict and set(arguments) == set(definition.argument_fields))
    if name == 'save_draft':
        from pacttrail.domain.change_draft import validate_draft_arguments
        return validate_draft_arguments(arguments, draft_source, creation_path=creation_path)
    field = definition.argument_fields[0]
    value = arguments[field]
    _require(_text(value, definition.max_chars, allow_cr=name == 'save_report'))
    if name == 'read' and (strict or file_ids != FILE_IDS):
        _require(value in file_ids)
    if name == 'search' and strict:
        _require(not any(ord(char) < 32 for char in value))
    if definition.max_bytes is not None:
        _require(len(value.encode('utf-8')) <= definition.max_bytes)
    return copy.deepcopy(arguments)


def tool_schemas(source=None, *, draft=False, creation_path=None):
    """Build fresh provider schemas from the same closed local definitions."""
    from pacttrail.domain.tool_task import source_file_ids, validate_source_descriptor
    selected = validate_source_descriptor(source) if source is not None else None
    _require(not draft or selected is not None and selected['kind'] == 'selected_project'
             and selected['version'] == 1)
    if creation_path is not None:
        from pacttrail.domain.change_draft import validate_creation_path
        _require(draft)
        validate_creation_path(creation_path, selected)
    file_ids = source_file_ids(selected)
    scope = 0 if selected is None else 2 if selected['kind'] == 'selected_project' else 1
    result = []
    for name in tool_names(draft=draft):
        definition = tool_definition(name)
        if name == 'save_draft':
            properties = {
                'source_sha256': {'type': 'string', 'enum': [selected['sha256']]},
                'changes': {'type': 'array', 'minItems': 1, 'maxItems': len(selected['files']),
                            'items': {'type': 'object', 'properties': {
                                'file_id': {'type': 'string', 'enum': list(file_ids)},
                                'base_sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
                                'new_text': {'type': 'string', 'minLength': 1, 'maxLength': 4096}},
                                'required': ['file_id', 'base_sha256', 'new_text'], 'additionalProperties': False}},
            }
            if creation_path is not None:
                from pacttrail.domain.change_draft import EMPTY_SHA256
                changes = properties['changes']
                changes['maxItems'] = 1
                changes['items']['properties']['file_id']['enum'] = ['new_file']
                changes['items']['properties']['base_sha256'] = {'type': 'string', 'enum': [EMPTY_SHA256]}
        else:
            schema = ({'type': 'string', 'enum': list(file_ids)} if name == 'read' else
                      {'type': 'string', 'maxLength': definition.max_chars})
            properties = {definition.argument_fields[0]: schema}
        properties['reason'] = {'type': 'string', 'maxLength': MAX_REASON_CHARS}
        result.append({'type': 'function', 'function': {
            'name': name, 'description': definition.descriptions[scope],
            'parameters': {'type': 'object', 'properties': properties,
                           'required': [*definition.argument_fields, 'reason'], 'additionalProperties': False}}})
    return result
