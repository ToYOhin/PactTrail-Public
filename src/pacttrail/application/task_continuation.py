"""Explain saved task facts as a new question; never resume an executor."""
from __future__ import annotations

import json

from pacttrail.domain.task_record import record_digest


def continuation_plan(detail, *, conversation_id, revision, task_id, busy=False):
    """Use a validated history detail, excluding model reasons and old authority."""
    record = detail['record']
    task = record['task']
    intent = task.get('intent', 'report')
    facts, unfinished, artifacts = [], [], []
    for step in task['steps']:
        if step['kind'] != 'tool':
            continue
        result, tool = step['result'], step['tool']
        if result['status'] != 'ok':
            unfinished.append(f"{tool} 未成功完成（{result['error']}）。")
        elif tool == 'search':
            facts.append(f"已完成一次来源搜索，记录 {len(result['matches'])} 条匹配。")
        elif tool == 'read':
            facts.append(f"已读取快照中的 {result['file_id']}。")
        elif tool in ('save_report', 'save_draft'):
            facts.append('已收到' + ('草案' if tool == 'save_draft' else '报告') + '保存回执。')

    artifact = task['artifact']
    path = None
    if artifact is not None:
        available = detail['integrity'] == 'verified' and detail['recovery'] != 'write_unknown'
        application = detail.get('application')
        application_state = application['state'] if application else 'none'
        if application_state not in ('applied', 'partial', 'unknown'):
            application_state = 'none'
        artifacts.append(dict(kind='change_draft' if intent == 'change_draft' else 'report',
            **artifact, available=available, application_state=application_state))
        if available and intent == 'change_draft' and detail.get('report'):
            draft = json.loads(detail['report']['text'])
            if draft['version'] == 2:
                path = draft['changes'][0]['path']
        if not available:
            unfinished.append('旧成果或来源尚未通过当前核对，请先在记录中核对。')
        if application_state == 'applied':
            facts.append('应用收据确认该草案已应用。')
        elif application_state in ('partial', 'unknown'):
            unfinished.append('文件应用' + ('仅部分完成。' if application_state == 'partial' else '结果待核对。'))

    if not facts:
        facts.append('记录中尚无已确认完成的工具步骤或成果。')
    if record['status'] == 'stopped':
        unfinished.append('任务已停止，原目标尚未记录为完成。')
    elif record['status'] == 'error':
        unfinished.append(f"任务失败（{record['error']}），原目标尚未记录为完成。")
    elif record['status'] == 'running':
        unfinished.append('记录停留在运行中，未记录最终结果。')
    if detail['recovery'] == 'write_unknown':
        unfinished.append('最后一次成果保存结果未知；请先核对，不能自动重试旧写入。')
    elif record['pending'] is not None:
        unfinished.append('最后一次处理未记录结果；续接不会重放该处理。')
    if detail['integrity'] in ('source_changed', 'both_changed'):
        unfinished.append('旧来源快照已变化或不可用；请重新选择并确认当前来源。')
    if intent == 'change_draft' and path is None:
        unfinished.append('如需新建文件，请在发送前重新核对并填写路径。')

    source = task['source']
    source_ref = dict(state='recheck_required' if source else 'none',
                      name=source['name'] if source else None,
                      sha256=source['sha256'] if source else None)
    message = ('请接着完成以下目标。先核对已有成果与当前来源；未确认的操作先核对，'
               '不要重放旧工具或重复已有成果。\n\n原目标：\n' + record['goal']
               + '\n\n已确认的历史事实：\n' + '\n'.join('- ' + item for item in facts)
               + '\n\n仍需处理：\n' + '\n'.join('- ' + item for item in unfinished))
    reason = 'busy' if busy else 'already_complete' if record['status'] == 'complete' else None
    suggestion = dict(message=message, task_intent=intent, creation_path=path)
    identity = dict(record=record, recovery=detail['recovery'], integrity=detail['integrity'],
                    application=detail.get('application'))
    return dict(version=1, kind='task_continuation', conversation_id=conversation_id,
        revision=revision, task_id=task_id, run_id=record['run_id'],
        record_sha256=record_digest(identity), status=record['status'], recovery=detail['recovery'],
        integrity=detail['integrity'], goal=record['goal'], facts=facts, unfinished=unfinished,
        artifacts=artifacts, source=source_ref, suggestion=suggestion,
        can_prepare=reason is None, reason=reason)
