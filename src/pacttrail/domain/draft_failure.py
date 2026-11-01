"""An explicit request about failed new draft text, never target syntax feedback."""
from __future__ import annotations

import json
import re
import copy
import hashlib

from pacttrail.domain.project_source import validate_project_path
from pacttrail.domain.tool_task import DecisionError, MAX_GOAL_CHARS


DRAFT_FAILURE_PREFIX = '修复未应用草案的 Python 语法失败；'


class DraftRepairUnsupported(DecisionError):
    """The offline fixture cannot repair an unapplied draft."""


def format_draft_failure_goal(check_id, run_id, artifact_sha256, failures):
    if (type(check_id) is not str or re.fullmatch(r'[a-f0-9]{32}', check_id) is None
            or type(run_id) is not str or re.fullmatch(r'[a-f0-9]{32}', run_id) is None
            or type(artifact_sha256) is not str or re.fullmatch(r'[a-f0-9]{64}', artifact_sha256) is None
            or type(failures) is not list or not 1 <= len(failures) <= 4):
        raise DecisionError()
    checked, paths = [], set()
    for failure in failures:
        if (type(failure) is not dict or set(failure) != {'path', 'line'}
                or type(failure['line']) is not int or not 1 <= failure['line'] <= 257):
            raise DecisionError()
        path = validate_project_path(failure['path'])
        if not path.lower().endswith('.py') or path.casefold() in paths:
            raise DecisionError()
        paths.add(path.casefold())
        checked.append(dict(path=path, line=failure['line']))
    goal = DRAFT_FAILURE_PREFIX + json.dumps(dict(check_id=check_id, run_id=run_id,
        artifact_sha256=artifact_sha256, failures=checked), ensure_ascii=False,
        separators=(',', ':'), allow_nan=False)
    if len(goal) > MAX_GOAL_CHARS:
        raise DecisionError()
    return goal


def parse_draft_failure_goal(goal):
    if type(goal) is not str or len(goal) > MAX_GOAL_CHARS or not goal.startswith(DRAFT_FAILURE_PREFIX):
        raise DecisionError()
    try:
        value = json.loads(goal[len(DRAFT_FAILURE_PREFIX):])
        if (type(value) is not dict or set(value) != {'check_id', 'run_id', 'artifact_sha256', 'failures'}
                or format_draft_failure_goal(**value) != goal):
            raise DecisionError()
    except (TypeError, ValueError, RecursionError):
        raise DecisionError() from None
    return value


def failure_draft(goal, context, source, *, originals=None):
    """Resolve exactly the unapplied artifact named by this explicit new goal."""
    from pacttrail.domain.change_draft import validate_draft
    from pacttrail.domain.tool_conversation import validate_context
    from pacttrail.domain.conversation_capacity import MAX_CONTEXT_BYTES

    parsed = parse_draft_failure_goal(goal)
    history = validate_context(context, max_bytes=MAX_CONTEXT_BYTES)
    matches = [turn for turn in history['turns'] if turn['run_id'] == parsed['run_id']]
    if len(matches) != 1:
        raise DecisionError()
    turn = matches[0]
    artifact = turn['artifact']
    if (turn['intent'] != 'change_draft' or turn['application'] is not None or artifact is None
            or artifact['sha256'] != parsed['artifact_sha256']
            or turn['source_kind'] != 'selected_project' or turn['source_sha256'] != source['sha256']):
        raise DecisionError()
    draft = validate_draft(artifact['text'], parsed['run_id'], source, originals=originals)
    paths = {change['path'] for change in draft['changes']}
    if any(failure['path'] not in paths for failure in parsed['failures']):
        raise DecisionError()
    return draft


def repair_draft_arguments(goal, context, source, originals, *, creation_path=None):
    """Only add the missing colon to checked lines in saved new text."""
    from pacttrail.domain.change_draft import validate_draft_arguments

    parsed = parse_draft_failure_goal(goal)
    draft = failure_draft(goal, context, source, originals=originals)
    expected_path = draft['changes'][0]['path'] if draft['version'] == 2 else None
    if creation_path != expected_path:
        raise DecisionError()
    changes = copy.deepcopy(draft['changes'])
    by_path = {change['path']: change for change in changes}
    for failure in parsed['failures']:
        change = by_path[failure['path']]
        parts = re.split(r'(\r\n|\r|\n)', change['new_text'])
        index = (failure['line'] - 1) * 2
        line = (re.fullmatch(r'([ \t]*def [A-Za-z_][A-Za-z0-9_]*\(\))([ \t]*)', parts[index])
                if index < len(parts) else None)
        if line is None:
            raise DraftRepairUnsupported()
        parts[index] = line[1] + ':' + line[2]
        change['new_text'] = ''.join(parts)
    args = dict(source_sha256=source['sha256'], changes=[{key: change[key]
                for key in ('file_id', 'base_sha256', 'new_text')} for change in changes])
    if draft['version'] == 1 and any(hashlib.sha256(change['new_text'].encode('utf-8')).hexdigest()
                                    == change['base_sha256'] for change in changes):
        raise DraftRepairUnsupported()  # Never invent content to manufacture a non-empty edit.
    return validate_draft_arguments(args, source, creation_path=creation_path)
