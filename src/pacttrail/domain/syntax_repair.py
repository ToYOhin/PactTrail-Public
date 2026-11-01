"""One bounded ordinary goal for an explicitly requested syntax repair draft."""
from __future__ import annotations

import re

from pacttrail.domain.tool_task import DecisionError


REPAIR_PREFIX = '修复 Python 语法验证失败；'


class SyntaxRepairUnsupported(DecisionError):
    """The checked failure line does not match the synthetic repair fixture."""


def format_repair_goal(check_id, project_sha256, failures):
    if (type(check_id) is not str or re.fullmatch(r'[a-f0-9]{32}', check_id) is None
            or type(project_sha256) is not str
            or re.fullmatch(r'[a-f0-9]{64}', project_sha256) is None
            or type(failures) is not list or not 1 <= len(failures) <= 4):
        raise DecisionError()
    entries, previous = [], 0
    for failure in failures:
        if (type(failure) is not dict or set(failure) != {'file_id', 'line'}
                or type(failure['file_id']) is not str
                or re.fullmatch(r'file-[1-4]', failure['file_id']) is None
                or type(failure['line']) is not int or not 1 <= failure['line'] <= 257):
            raise DecisionError()
        number = int(failure['file_id'][-1])
        if number <= previous:
            raise DecisionError()
        previous = number
        entries.append(f"{failure['file_id']}:{failure['line']}")
    goal = REPAIR_PREFIX + 'check=' + check_id + '；sha=' + project_sha256 + '；failed=' + ','.join(entries)
    if len(goal) > 256:
        raise DecisionError()
    return goal


def parse_repair_goal(goal):
    if type(goal) is not str or len(goal) > 256 or not goal.startswith(REPAIR_PREFIX):
        raise DecisionError()
    match = re.fullmatch(re.escape(REPAIR_PREFIX)
                         + r'check=([a-f0-9]{32})；sha=([a-f0-9]{64})；failed=(.+)', goal)
    if match is None:
        raise DecisionError()
    failures = []
    for entry in match[3].split(','):
        item = re.fullmatch(r'(file-[1-4]):([1-9][0-9]{0,2})', entry)
        if item is None:
            raise DecisionError()
        failures.append(dict(file_id=item[1], line=int(item[2])))
    value = dict(check_id=match[1], project_sha256=match[2], failures=failures)
    if format_repair_goal(**value) != goal:
        raise DecisionError()
    return value
