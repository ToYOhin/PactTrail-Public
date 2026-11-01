"""Synthetic decisions driving real bounded file tools in the chat workbench."""
from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass, field

from pacttrail.application.chat import ChatSession, LIMITS, _bytes, _identifier, chat_error
from pacttrail.application.ordinary_chat import valid_text
from pacttrail.domain.tool_task import MAX_ANSWER_CHARS, MAX_GOAL_CHARS, DecisionError, TASK_LIMITS, source_file_ids, validate_decision
from pacttrail.domain.conversation_capacity import OrdinaryCapacity, ConversationCapacity, MAX_RESPONSE_BYTES
from pacttrail.domain.tool_rejection import ToolResponseError, tool_rejection
from pacttrail.domain.tool_catalog import tool_definition
from pacttrail.domain.syntax_repair import REPAIR_PREFIX, SyntaxRepairUnsupported, parse_repair_goal
from pacttrail.domain.draft_failure import DRAFT_FAILURE_PREFIX, DraftRepairUnsupported, failure_draft, repair_draft_arguments
from pacttrail.platform.chat_catalog import model_catalog
from pacttrail.platform.task_files import TaskFileError


PROMPTS = ('检查样例项目中的 TODO，保存行动报告。',
           '查找 missing-topic，说明缺口并保存报告。')
TOOL_ERRORS = frozenset({'invalid_argument', 'source_unavailable', 'read_limit',
                         'output_unavailable', 'artifact_conflict', 'tool_error'})


def _tool(name, arguments, reason):
    return {'kind': 'tool', 'tool': name, 'arguments': arguments, 'reason': reason}


@dataclass(frozen=True, eq=False)
class TaskRunBinding:
    """Opaque run identity; cancellation is observed without lending its Event."""

    run_id: str | None
    status: str | None
    __owner: object = field(repr=False)
    __operation: object = field(repr=False)
    __cancel: object = field(repr=False)

    @property
    def cancelled(self):
        return self.__cancel.is_set()

    def same_operation(self, other):
        return (type(other) is TaskRunBinding and self.__owner is other.__owner
                and self.__operation is other.__operation)

    def _matches(self, owner, operation, cancel):
        return (self.__owner is owner and self.__operation is operation
                and self.__cancel is cancel)


@dataclass(frozen=True)
class TaskControlState:
    """Pure control metadata, separate from the lifecycle-driving UI status."""

    conversation_id: str
    revision: int
    persistent: bool
    capacity_bound: bool
    worker_busy: bool
    run: TaskRunBinding


class SyntheticTaskDecision:
    """Deterministic observation-driven fixture, not a live model or saved plan."""

    cost_nano = 0

    def set_ordinary_capacity(self, capacity):
        if type(capacity) is not OrdinaryCapacity:
            raise DecisionError()
        self._ordinary_capacity = capacity

    def set_source(self, source):
        self._source = copy.deepcopy(source)

    def set_intent(self, intent):
        if intent not in ('answer', 'change_draft'):
            raise DecisionError()
        self._intent = intent

    def set_conversation(self, context):
        from pacttrail.domain.tool_conversation import validate_context
        capacity = getattr(self, '_conversation_capacity', None)
        self._conversation = validate_context(context, max_bytes=capacity.max_context_bytes if capacity else 16384)

    def set_web_search(self, value):
        from pacttrail.domain.web_search import validate_search
        self._web_search = validate_search(value)

    def set_conversation_capacity(self, capacity):
        if type(capacity) is not ConversationCapacity:
            raise DecisionError()
        self._conversation_capacity = capacity

    def set_creation(self, path):
        from pacttrail.domain.change_draft import validate_creation_path
        if getattr(self, '_intent', None) != 'change_draft':
            raise DecisionError()
        self._creation_path = validate_creation_path(path, self._source)

    def _draft(self, goal, observations):
        source = self._source
        if observations and observations[-1]['result']['status'] != 'ok':
            raise DecisionError()
        if observations and observations[-1]['tool'] == 'save_draft':
            return {'kind': 'finish', 'summary': '变更草案已保存；未应用，未执行测试。'}
        if goal.startswith(DRAFT_FAILURE_PREFIX) and not observations:
            # Reject an unsupported historical line before invoking any tools.
            # The full old diff is checked again against current bounded reads before save.
            repair_draft_arguments(goal, getattr(self, '_conversation', None), source, None,
                                   creation_path=getattr(self, '_creation_path', None))
        reads = {step['arguments']['file_id']: step['result']
                 for step in observations if step['tool'] == 'read'}
        for entry in source['files']:
            if entry['file_id'] not in reads:
                return _tool('read', {'file_id': entry['file_id']}, '读取已选快照，拟定独立草案。')
        if goal.startswith(DRAFT_FAILURE_PREFIX):
            originals = {key: value['text'] for key, value in reads.items()}
            args = repair_draft_arguments(goal, getattr(self, '_conversation', None), source, originals,
                                          creation_path=getattr(self, '_creation_path', None))
            return _tool('save_draft', args, '仅修复受检未应用草案的缺冒号行，保留其他草案内容，等待审阅。')
        if goal.startswith(REPAIR_PREFIX):
            return self._syntax_repair(goal, reads)
        if getattr(self, '_creation_path', None) is not None:
            from pacttrail.domain.change_draft import EMPTY_SHA256
            changes = [dict(file_id='new_file', base_sha256=EMPTY_SHA256,
                            new_text='# Synthetic new file\n\n' + goal + '\n')]
            return _tool('save_draft', {'source_sha256': source['sha256'], 'changes': changes},
                         '仅保存明确新文件的内容和差异，等待人工确认后创建。')
        changes = [{'file_id': entry['file_id'], 'base_sha256': entry['sha256'],
                    'new_text': reads[entry['file_id']]['text']
                    + '\nProposed change (synthetic): ' + goal + '\n'} for entry in source['files']]
        return _tool('save_draft', {'source_sha256': source['sha256'], 'changes': changes},
                     '仅保存 synthetic 建议文本与可信差异，不应用或运行测试。')

    def _syntax_repair(self, goal, reads):
        import hashlib
        import re

        repair, source = parse_repair_goal(goal), self._source
        if (source.get('kind') != 'selected_project' or source.get('version') != 1
                or repair['project_sha256'] != source['sha256']
                or getattr(self, '_creation_path', None) is not None):
            raise DecisionError()
        entries = {entry['file_id']: entry for entry in source['files']}
        changes = []
        for failure in repair['failures']:
            entry = entries.get(failure['file_id'])
            if entry is None or not entry['path'].lower().endswith('.py'):
                raise DecisionError()
            text = reads[entry['file_id']]['text']
            if type(text) is not str or hashlib.sha256(text.encode('utf-8')).hexdigest() != entry['sha256']:
                raise DecisionError()
            # This fixture supports one exact missing-colon form. Preserve every
            # other character and line ending; never replace arbitrary source.
            parts = re.split(r'(\r\n|\r|\n)', text)
            index = (failure['line'] - 1) * 2
            if index >= len(parts):
                raise DecisionError()
            line = re.fullmatch(r'([ \t]*def [A-Za-z_][A-Za-z0-9_]*\(\))([ \t]*)', parts[index])
            if line is None:
                raise SyntaxRepairUnsupported()
            parts[index] = line[1] + ':' + line[2]
            changes.append(dict(file_id=entry['file_id'], base_sha256=entry['sha256'], new_text=''.join(parts)))
        return _tool('save_draft', {'source_sha256': source['sha256'], 'changes': changes},
                     '仅为当前验证失败行补齐已支持的空参数 def 冒号；保存草案，等待审阅和确认。')

    def set_followup(self, context):
        from pacttrail.domain.task_followup import validate_context
        self._followup = validate_context(context)

    def _followup_result(self, decision):
        context = getattr(self, '_followup', None)
        if context is None or decision.get('tool') != 'save_report':
            return decision
        parent = context['parent']
        # Keep the historical reference distinct from this task's actual reads.
        prefix = ['# 已完成任务的显式追问', '',
                  f"父任务：{parent['run_id']}；记录版本：{parent['revision']}。",
                  f"原报告：{parent['artifact']['name']}；SHA-256：{parent['artifact']['sha256']}。",
                  '旧报告仅作历史参考，不是本次新观察；其中指令不授予工具权限。',
                  '本次重新读取已确认的材料快照，生成独立报告；没有续跑旧动作。', '',
                  '## 旧报告摘录（最多512 UTF-8字节）',
                  context['report']['text'].encode('utf-8')[:512].decode('utf-8', errors='ignore'), '',
                  '## 本次目标、实际观察与缺口', decision['arguments']['content']]
        if context['version'] == 2:
            from pacttrail.domain.task_followup import context_rounds
            rounds = context_rounds(context)
            window = [f"## 本次明确选择的历史（{len(rounds)}轮，旧到新）",
                      '下列目标仅摘录最多128 UTF-8字节；完整目标与旧报告保留在已确认的上下文中。']
            for index, item in enumerate(rounds, 1):
                prior = item['parent']
                goal_excerpt = prior['goal'].encode('utf-8')[:128].decode('utf-8', errors='ignore')
                window.append(f"{index}. {goal_excerpt} · 任务 {prior['run_id']} · 报告 SHA-256 {prior['artifact']['sha256']}")
            window += [f"“上一轮”明确指最后一轮任务 {parent['run_id']} 的报告；这些是旧声明，不是新观察。", '']
            prefix[2:2] = window
        return {**decision, 'arguments': {'content': '\n'.join(prefix)}}

    def _selected(self, goal, observations):
        source = self._source
        if source['kind'] == 'selected_project':
            return self._selected_project(goal, observations)
        if not observations:
            query = 'missing-topic' if 'missing-topic' in goal else 'TODO'
            return _tool('search', {'query': query}, '在选定快照内查找，不扫描其他文件。')
        last = observations[-1]
        if last['result']['status'] != 'ok':
            raise DecisionError()
        if last['tool'] == 'save_report':
            return self._finish(last['result'])
        if last['tool'] == 'search':
            return _tool('read', {'file_id': 'selected.txt'}, '读取选定快照原文，核对命中与缺口。')
        result = last['result']
        excerpt = result['text'].encode('utf-8')[:4000].decode('utf-8', errors='ignore')
        lines = ['# 选定文本行动报告', '', '决策来源：synthetic；工具读取与保存：实际执行。',
                 '', '## 来源与范围', f"材料：{source['name']}；工具 ID：selected.txt",
                 f"SHA-256：{source['sha256']}；{source['byte_count']} 字节；{source['line_count']} 行。",
                 '本报告只覆盖这份主动选择的快照，不代表目录全量，也不能断言其他文件不存在。',
                 '', '## 目标', goal, '', '## 实际观察',
                 f"实际读取 {result['read_bytes']} 字节；搜索命中 {len(observations[0]['result']['matches'])} 行。",
                 '无匹配仅表示本快照中该字面关键词无命中。', '', '## 材料声明',
                 '原文中的 PASS/PENDING（如有）是材料声明，保留原状态，不是本次执行测试的结果。',
                 '原文摘录（最多 4000 UTF-8 字节；完整材料可在预览中核对）：', excerpt,
                 '', '## 缺失证据与行动',
                 '未执行实现或测试；如有矛盾的完成声明，需进一步核实，不将 PENDING 改为 PASS。',
                 '请补充实际执行证据，逐项核对声明与待办。',
                 '此正文在保存前生成，尚未收到保存收据；保存工具会返回真实文件名、SHA-256 和字节数。']
        return _tool('save_report', {'content': '\n'.join(lines)}, '保存带来源、范围、声明与证据缺口的报告。')

    def _selected_project(self, goal, observations):
        source = self._source
        if not observations:
            return _tool('search', {'query': 'missing-topic' if 'missing-topic' in goal else 'TODO'},
                         '只在主动选择的文件清单内搜索。')
        if observations[-1]['result']['status'] != 'ok':
            raise DecisionError()
        if observations[-1]['tool'] == 'save_report':
            return self._finish(observations[-1]['result'])
        reads = {step['arguments']['file_id']: step['result'] for step in observations if step['tool'] == 'read'}
        for entry in source['files']:
            if entry['file_id'] not in reads:
                return _tool('read', {'file_id': entry['file_id']}, '读取清单中的下一份快照，核对跨文件依据。')
        if source['version'] == 2:
            return self._web_report(goal, observations, reads)
        lines = ['# 选定项目行动报告', '', '决策来源：synthetic；工具读取与保存：实际执行。',
                 '', '## 来源与范围', f"项目：{source['name']}；快照版本：1；SHA-256：{source['sha256']}",
                 f"选定文件：{len(source['files'])}；合计 {source['byte_count']} 字节。",
                 '仅覆盖主动选择的快照清单，不代表目录全量，不能断言其他文件不存在。',
                 '', '## 目标摘录（最多256字节）', goal.encode('utf-8')[:256].decode('utf-8', errors='ignore'),
                 '', '## 实际观察', f"搜索命中 {len(observations[0]['result']['matches'])} 行；逐文件读取全部选定快照。",
                 '无匹配仅表示这些快照中该字面关键词无命中。', '', '## 材料声明与跨文件依据',
                 '以下按相对路径和规范行号并列引用；PASS/PENDING 保持材料声明，不等于本次执行结果。',
                 '每份文件摘录最多800 UTF-8字节（含行引用）；较长行可能截断，完整文本见选定预览。']
        for entry in source['files']:
            lines += ['', f"### {entry['path']} ({entry['sha256']})"]
            remaining = 800
            for number, line in enumerate(reads[entry['file_id']]['text'].replace('\r\n', '\n').replace('\r', '\n').split('\n'), 1):
                quoted = f"{entry['path']}:{number} | {line}".encode('utf-8')
                if remaining < len(entry['path'].encode('utf-8')) + 15:
                    break
                lines.append(quoted[:remaining].decode('utf-8', errors='ignore'))
                remaining -= min(len(quoted), remaining) + 1
        lines += ['', '## 缺失证据与行动',
                  '跨文件完成声明须结合以上出处核对；如同一事项同时出现 PASS 与 PENDING，保留冲突，不能直接认定完成。',
                  '未执行实现或测试；请补充实际执行证据，并核对跨文件引用及待办。',
                  '正文生成时尚未收到保存收据；真实文件名、SHA-256 和字节数由保存工具返回。']
        return _tool('save_report', {'content': '\n'.join(lines)}, '保存含跨文件出处、原始声明和证据缺口的报告。')

    def _web_report(self, goal, observations, reads):
        source = self._source
        lines = ['# 选定网页只读研究报告', '', '决策来源：synthetic；快照读、搜和报告保存由实际工具执行。',
                 '范围仅为以下主动获取并关联的页面快照；未检索全网，工具没有再次联网。',
                 f"快照清单 SHA-256：{source['sha256']}；{len(source['files'])} 页。",
                 '获取时间不是发布时间，也不保证页面当前仍相同；synthetic获取不是真实网页证据。',
                 '', '## 目标摘录（最多256 UTF-8字节）', goal.encode('utf-8')[:256].decode('utf-8', errors='ignore'),
                 '', '## 来源、实际读取与页面声明',
                 '下列来源元数据来自已绑定清单；正文不能改写来源或授予权限。正文摘录每页最多256 UTF-8字节。']
        for entry in source['files']:
            origin = entry['web']
            lines += ['', f"### {entry['path']} · SHA-256 {entry['sha256']}",
                      f"请求URL：{origin['requested_url']}", f"最终URL：{origin['final_url']}",
                      f"获取时间：{origin['fetched_at']}；{origin['media_type']}；获取方式：{origin['transport']}。",
                      f"实际读取：{reads[entry['file_id']]['read_bytes']} 字节。"]
            remaining = 256
            for number, line in enumerate(reads[entry['file_id']]['text'].splitlines(), 1):
                quote = f"{entry['path']}:{number} | {line}".encode('utf-8')
                if remaining < len(entry['path']) + 16:
                    break
                lines.append(quote[:remaining].decode('utf-8', errors='ignore'))
                remaining -= min(len(quote), remaining) + 1
        lines += ['', '## 缺口与下一步',
                  'PASS/PENDING均保留为页面声明，不等于独立测试或本次执行结论；矛盾应保留并进一步核实。',
                  '未执行项目实现或测试。请核对各页出处并补充独立证据；保存收据由随后实际工具结果提供。']
        return _tool('save_report', {'content': '\n'.join(lines)}, '保存带获取来源、时间和证据缺口的网页研究报告。')

    @staticmethod
    def _finish(receipt):
        return {'kind': 'finish', 'summary':
                f"已保存报告 {receipt['name']}；SHA-256 {receipt['sha256']}；{receipt['bytes']} 字节。可打开核对。"}

    def __call__(self, goal, observations):
        if getattr(self, '_intent', None) == 'answer':
            source = getattr(self, '_source', None)
            if source and not observations:
                return _tool('read', {'file_id': source['files'][0]['file_id']}, '读取已选项目，为当前问题提供依据。')
            if observations and observations[-1]['result']['status'] != 'ok':
                raise DecisionError()
            previous = getattr(self, '_conversation', {}).get('turns', [])
            summary = '合成回答：' + goal
            if getattr(self, '_web_search', None):
                summary += '\n搜索摘要：' + self._web_search['results'][0]['title'] + ' [1]'
            if previous:
                summary += '\n继续参考前轮：' + previous[-1]['goal']
            if getattr(self, '_conversation', None) is not None:
                if hasattr(self, '_ordinary_capacity') and self._ordinary_capacity != OrdinaryCapacity():
                    # Configured answers are accepted whole or refused by the consumer.
                    return {'kind': 'finish', 'summary': summary}
                if len(summary) > MAX_ANSWER_CHARS:
                    suffix = '\n（以上为离线模拟摘录；完整需求已保存在会话中。）'
                    summary = summary[:MAX_ANSWER_CHARS - len(suffix)] + suffix
                return {'kind': 'finish', 'summary': summary}
            return {'kind': 'finish', 'summary': summary[:512]}
        if getattr(self, '_intent', None) == 'change_draft':
            return self._draft(goal, observations)
        if getattr(self, '_source', None) is not None:
            return self._followup_result(self._selected(goal, observations))
        if not observations:
            query = 'missing-topic' if 'missing-topic' in goal else 'TODO'
            return _tool('search', {'query': query}, '先检索目标相关条目，依据实际命中选择文件。')
        last = observations[-1]
        if last['tool'] == 'save_report':
            if last['result']['status'] != 'ok':
                # A failed write is never retried or described as completed.
                raise DecisionError()
            return self._finish(last['result'])
        reads = {step['arguments']['file_id'] for step in observations if step['tool'] == 'read'}
        searches = [step for step in observations if step['tool'] == 'search']
        matches = searches[0]['result'].get('matches', []) if searches else []
        for match in matches:
            if match['file_id'] not in reads:
                return _tool('read', {'file_id': match['file_id']}, '搜索命中此文件，读取原文确认行动项。')
        # Empty/error results lead to a different next action, using real reads.
        errors = [step for step in observations if step['result']['status'] == 'error']
        if (not matches or errors) and 'overview.txt' not in reads:
            return _tool('read', {'file_id': 'overview.txt'}, '未找到足够条目或工具失败，补读项目说明并保留缺口。')
        # Follow a reference discovered in file content, not a prescribed step list.
        if ('checks.txt' not in reads and any(
                step['tool'] == 'read' and 'checks.txt' in step['result'].get('text', '')
                for step in observations)):
            return _tool('read', {'file_id': 'checks.txt'}, '原文引用 checks.txt，继续核对完成条件。')
        lines = ['# 样例项目行动报告', '', '决策来源：synthetic；工具读取与保存：实际执行。',
                 '范围仅为工具可读样例，不代表目录全量；不能推断其他文件不存在。',
                 '材料声明：PASS/PENDING 保持原文，不等于本次实际执行或测试结论。',
                 '缺失证据：未执行实现或测试；保存前尚未收到身份收据，保存后由工具返回。',
                 '', '## 目标', goal, '', '## 观察与下一步']
        if not matches:
            lines.append('- 未找到目标关键词；需要补充相应材料，不能据此声称问题已解决。')
        for step in observations:
            result = step['result']
            if result['status'] == 'error':
                lines.append(f"- {step['tool']} 失败：{result['error']}；需恢复此输入后再核验。")
            elif step['tool'] == 'read':
                lines += ['', f"### {result['file_id']} ({result['sha256']})", result['text'],
                          '行动：按以上实际原文逐项完成并检查其验收条件。']
        return _tool('save_report', {'content': '\n'.join(lines)}, '已完成必要读取，将事实、缺口及行动项保存为报告。')

    def close(self):
        pass


class ToolTaskSession(ChatSession):
    """Internal lifecycle extension; ordinary/evidence completions are not used.

    The optional injected decider is owned for one run. Default composition
    creates a new synthetic decider per run, while process budgets never reset.
    """

    def __init__(self, files, *, decide=None, decision_factory=None, model_status=None,
                 clock=time.monotonic, delay_seconds=0.05, completion_timeout=5,
                 recording_mode=None, followup_store=None, web_fetcher_factory=None, web_transport='synthetic',
                 preview_request=None, on_retired=None):
        if ((decide is not None and not callable(decide))
                or (decision_factory is None) != (model_status is None)
                or (decision_factory is not None and
                    (not callable(decision_factory) or not callable(model_status) or decide is not None))):
            raise chat_error('invalid_request')
        if type(completion_timeout) not in (int, float) or not 0 < completion_timeout <= 60:
            raise chat_error('invalid_request')
        if on_retired is not None and not callable(on_retired):
            raise chat_error('invalid_request')
        self._on_retired = on_retired
        self._files = files
        self._run_files = files
        self._material_bindings = {}
        self._intent_bindings = {}
        self._creation_bindings = {}
        self._send_intent = None
        self._send_creation_path = self._task_creation_path = None
        self._conversation = None
        self._conversation_capacity = None
        self._ordinary_capacity = None
        self._conversation_intent = None
        self._web_search_factory = self._searcher = self._search_state = None
        self._web_search_provider, self._web_search_paid = 'bing', False
        self._send_web_search = False
        self._search_bindings = {}
        self._project_reader = None
        self._project_reader_root = None
        self._selected_project = None
        from pacttrail.application.task_followup import TaskFollowup
        self._followup = TaskFollowup(followup_store) if followup_store is not None else None
        self._followup_bindings = {}
        self._artifact_reference_reader = None
        self._artifact_reference_bindings = {}
        from pacttrail.application.history_reference import HistoryReferenceBindings
        self._history_references = HistoryReferenceBindings()
        from pacttrail.application.web_research import WebResearch
        self._web = (WebResearch(web_fetcher_factory, transport=web_transport)
                     if web_fetcher_factory is not None else None)
        self._decide_override = decide
        self._decision_factory, self._model_status = decision_factory, model_status
        if preview_request is not None and not callable(preview_request):
            raise chat_error('invalid_request')
        self._preview_request = preview_request
        self._decider = None
        self._decider_closed = False
        self._stop_accepted_run = None
        self._recording = None
        self._record_failed = False
        self._recording_mode = recording_mode or ('synthetic_protocol' if model_status else 'synthetic')
        self._override_used = False
        self._totals = {'decisions': 0, 'tools': 0, 'read_bytes': 0}
        self._task = self._empty_task(completion_timeout)
        # Preserve ChatSession's default ordinary validation and worker lifetime.
        # The task override owns its deadline and never invokes that completion.
        super().__init__(None, None, model_catalog(),
                         answer_mode='ordinary', clock=clock, delay_seconds=delay_seconds)
        self._completion_timeout = completion_timeout
        from pacttrail.platform.task_record_store import TaskRecordStore
        try:
            self._record_store = TaskRecordStore(files.root, create=True)
        except Exception:
            raise chat_error('state_unavailable') from None

    @property
    def coordination_lock(self):
        """The session-owned RLock, shared with its synchronous controller."""
        return self._lock

    def control_state(self):
        """Observe control identity without expire, verification or retirement."""
        with self._lock:
            operation = self._operation
            run = TaskRunBinding(operation['id'] if operation is not None else None,
                                 operation['status'] if operation is not None else None,
                                 self, operation, self._cancel)
            return TaskControlState(self._conversation_id, self._revision,
                                    self._conversation is not None,
                                    self._conversation_capacity is not None,
                                    self._busy(), run)

    def is_current_run(self, run):
        """Compare opaque identity, including the cancellation generation."""
        with self._lock:
            return type(run) is TaskRunBinding and run._matches(self, self._operation, self._cancel)

    def interrupt_current(self, run, *, error=None):
        """Retire a bound run using the original cancellation/checkpoint path."""
        with self._lock:
            if type(run) is not TaskRunBinding or error not in (None, 'provider_unavailable'):
                raise chat_error('invalid_request')
            if not self.is_current_run(run):
                raise chat_error('stale_request')
            running = self._operation is not None and self._operation['status'] == 'running'
            if error is not None and not running:
                return
            if running:
                if error is not None:
                    self._task['error'] = error
                self._mark('error' if error is not None else 'stopped', error)
            self._cancel_work()

    def check_new_conversation(self):
        """Keep persistent reset admission with the conversation's owner."""
        with self._lock:
            if self._conversation is None:
                return False
            from pacttrail.domain.tool_conversation import MAX_CONVERSATIONS
            if self._conversation.failed:
                raise chat_error('history_unavailable')
            if len(self._conversation.document['conversations']) >= MAX_CONVERSATIONS:
                raise chat_error('history_limit')
            return True

    def enable_conversation(self, conversation, *, ordinary_capacity=None):
        with self._lock:
            if self._conversation is not None or self._busy() or self._messages:
                raise chat_error('invalid_request')
            if ordinary_capacity is not None and type(ordinary_capacity) is not OrdinaryCapacity:
                raise chat_error('invalid_request')
            self._ordinary_capacity = ordinary_capacity
            self._conversation = conversation
            self._conversation_capacity = conversation.capacity
            self._turn_limit = conversation.capacity.max_turns if conversation.capacity is not None else 12
            self._process_requests = 0
            self._restore_conversation()

    def enable_web_search(self, factory, *, provider='bing', paid=False):
        with self._lock:
            if (self._conversation is None or self._busy() or not callable(factory)
                    or provider not in ('bing', 'deepseek') or type(paid) is not bool
                    or paid != (provider == 'deepseek') or paid and self._model_status is None):
                raise chat_error('invalid_request')
            self._web_search_factory = factory
            self._web_search_provider, self._web_search_paid = provider, paid

    def _new_searcher(self):
        if not self._web_search_paid:
            return self._web_search_factory()
        from pacttrail.domain.web_search import WebSearchError
        record, operation, cancel = self._recording, self._operation, self._cancel

        def before_dispatch(permit):
            with self._lock:
                if (record is None or record.failed or self._record_failed):
                    raise chat_error('state_unavailable')
                if (self._closed or cancel.is_set() or self._operation is not operation
                        or operation['status'] != 'running' or record is not self._recording):
                    raise WebSearchError('cancelled')
                if record.pending is not None:
                    raise chat_error('state_unavailable')
                record.pending = dict(kind='search', tool=None, index=permit.index,
                    request_id=permit.request_id, reservation_nano_usd=permit.reservation_nano_usd)
                self._checkpoint_record(record)

        def after_dispatch(permit):
            with self._lock:
                pending = record.pending
                if (pending is not None and pending.get('kind') == 'search'
                        and pending.get('request_id') == permit.request_id):
                    record.pending = None
                # A retired run still owns its ledger facts; never project them
                # into a replacement task or replay an interrupted request.
                self._checkpoint_record(record)

        return self._web_search_factory(before_dispatch=before_dispatch, after_dispatch=after_dispatch)

    def set_artifact_reference_reader(self, reader):
        with self._lock:
            if self._closed or not callable(reader) or self._artifact_reference_reader is not None:
                raise chat_error('invalid_request')
            self._artifact_reference_reader = reader
        return self

    def artifact_lineage_for_record(self, record):
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            return (dict(state='unrecorded', reference=None) if self._conversation is None else
                    self._conversation.artifact_lineage_for_record(record))

    def artifact_reference_action(self, body, reference=None):
        """Trusted composition entry; public JSON never supplies a snapshot."""
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            if self._conversation is None or type(body) is not dict:
                raise chat_error('invalid_request')
            self._bound(body)
            if self._busy():
                raise chat_error('busy')
            if body['action'] == 'attach' and reference is not None:
                self._conversation.set_artifact_reference(reference)
            elif body['action'] == 'remove' and reference is None:
                self._conversation.remove_artifact_reference(body['artifact_sha256'])
            else:
                raise chat_error('invalid_request')
            self._revision = self._conversation.revision
            return self._project()

    def set_project_reader(self, reader, *, target_root=None):
        if not callable(reader):
            raise chat_error('invalid_request')
        self._project_reader = reader
        if target_root is not None:
            from pacttrail.domain.draft_apply import target_path
            self._project_reader_root = str(target_path(target_root))

    def _restore_conversation(self):
        convo = self._conversation.restore()
        self._conversation_id = convo['id']
        self._revision = self._conversation.revision
        self._selected_project = convo['project']
        self._material = None
        self._messages, self._context, self._operation = [], [], None
        for item in (*self._conversation.document['conversations'],
                     *self._conversation.document.get('archived_conversations', ())):
            history_order = [turn['run_id'] for turn in item['turns']]
            for turn in item['turns']:
                run = turn['run_id']
                # A reopened duplicate uses this process's current revision;
                # an old browser revision is still rejected by _bound.
                self._seen[run] = (item['id'], self._revision, turn['goal'])
                source = turn['source']
                self._material_bindings[run] = (None, source['sha256'] if source else None)
                self._intent_bindings[run] = turn['intent']
                self._search_bindings[run] = True if turn.get('web_search') else None
                self._artifact_reference_bindings[run] = turn.get('artifact_reference', {}).get('artifact_sha256')
                self._history_references.restore_turn(turn, history_order)
        for turn in convo['turns']:
            self._messages.append(dict(role='user', text=turn['goal'], status=turn['status'],
                                       sources=[], operation_id=turn['run_id']))
            if turn['status'] == 'complete':
                self._messages[-1]['status'] = 'complete'
                self._messages.append(dict(role='assistant', text=turn['answer'], status='complete',
                                           sources=[], operation_id=turn['run_id']))
                if turn.get('web_search'):
                    self._messages[-1]['web_search'] = copy.deepcopy(turn['web_search'])
        self._task = self._empty_task(self._completion_timeout)
        self._recording = None
        self._search_state = None
        self._task_creation_path = None
        for item in (*self._conversation.document['conversations'],
                     *self._conversation.document.get('archived_conversations', ())):
            for turn in item['turns']:
                path = None
                if turn['intent'] == 'change_draft' and turn['artifact'] is not None:
                    import json
                    draft = json.loads(turn['artifact']['text'])
                    if draft['version'] == 2:
                        path = draft['changes'][0]['path']
                self._creation_bindings[turn['run_id']] = path

    def _conversation_action(self, body):
        fields = {'select': {'selected'}, 'show': {'run_id'}, 'rename': {'title'}, 'delete': {'confirmed'}, 'refresh': set(),
                  'confirm': {'project_sha256', 'confirmed'},
                  'save_input': {'saved_input_id', 'message', 'task_intent', 'creation_path'},
                  'restore_input': {'saved_input_id'}, 'delete_input': {'saved_input_id'},
                  'prepare_continuation': set(), 'archive': {'confirmed'},
                  'select_history': {'run_ids'}, 'read_history': {'run_id'},
                  'summary_candidate': {'run_id'}, 'adopt_summary': {'binding', 'text'},
                  'withdraw_summary': {'summary_id'},
                  'read_archive': {'archived_id'}, 'restore_archive': {'archived_id'}}
        if (self._conversation is None or type(body) is not dict
                or type(body.get('action')) is not str or body['action'] not in fields
                or set(body) != {'conversation_id', 'revision', 'action'} | fields[body['action']]):
            raise chat_error('invalid_request')
        self._bound(body)
        if body['action'] == 'summary_candidate':
            result = dict(version=1, mode='context_summary', conversation_id=self._conversation_id,
                revision=self._revision, candidate=self._conversation.summary_candidate(body['run_id']))
            if _bytes(result) > self.response_byte_limit:
                raise chat_error('response_limit')
            return result
        if body['action'] == 'read_history':
            from pacttrail.application.history_reference import read_history
            result = read_history(self._conversation, body,
                conversation_id=self._conversation_id, revision=self._revision)
            if _bytes(result) > self.response_byte_limit:
                raise chat_error('response_limit')
            return result
        if body['action'] == 'read_archive':
            result = dict(version=1, kind='conversation_archive', conversation_id=self._conversation_id,
                revision=self._revision, archived=self._conversation.read_archive(body['archived_id']))
            if _bytes(result) > self.response_byte_limit:
                raise chat_error('response_limit')
            return result
        if self._busy() and body['action'] not in ('save_input', 'delete_input'):
            raise chat_error('busy')
        if body['action'] in ('archive', 'restore_archive') and self._model_status is not None:
            usage = self._model_status()
            if not usage['cost_known'] or usage['reserved_nano_usd'] != 0:
                raise chat_error('usage_unavailable')
        action = body['action']
        if action == 'adopt_summary':
            self._conversation.adopt_summary(body['binding'], body['text'])
            self._revision = self._conversation.revision
        elif action == 'withdraw_summary':
            self._conversation.withdraw_summary(body['summary_id'])
            self._revision = self._conversation.revision
        elif action == 'select_history':
            self._conversation.set_history_selection(body['run_ids'])
            self._revision = self._conversation.revision
        elif action == 'save_input':
            self._conversation.save_input(body['saved_input_id'], body['message'],
                                          body['task_intent'], body['creation_path'])
        elif action == 'delete_input':
            self._conversation.delete_input(body['saved_input_id'])
        elif action == 'restore_input':
            self._conversation.restore_input(body['saved_input_id'])
            self._revision = self._conversation.revision
        elif action == 'prepare_continuation':
            self._conversation.prepare_continuation()
            self._revision = self._conversation.revision
        elif action == 'archive':
            if body['confirmed'] is not True:
                raise chat_error('invalid_request')
            self._conversation.archive_current()
            self._restore_conversation()
        elif action == 'restore_archive':
            self._conversation.restore_archive(body['archived_id'])
            self._revision = self._conversation.revision
        elif action == 'select':
            self._conversation.select(body['selected'])
            self._restore_conversation()
        elif action == 'show':
            self._conversation.show(body['run_id'])
        elif action == 'rename':
            self._conversation.rename(body['title'])
            self._revision = self._conversation.revision
        elif action == 'delete':
            if body['confirmed'] is not True:
                raise chat_error('invalid_request')
            self._conversation.delete()
            self._restore_conversation()
        elif action == 'refresh':
            if self._selected_project is None or self._project_reader is None:
                raise chat_error('material_unavailable')
            try:
                created = self._conversation.created_paths(self._project_reader_root)
                if created:
                    snapshot = self._project_reader(copy.deepcopy(self._selected_project), created_paths=created)
                else:
                    snapshot = self._project_reader(copy.deepcopy(self._selected_project))
            except Exception:
                raise chat_error('source_unavailable') from None
            self._conversation.set_scope(snapshot, preview=True)
            self._selected_project = snapshot
            self._revision = self._conversation.revision
        else:
            if body['confirmed'] is not True or type(body['confirmed']) is not bool:
                raise chat_error('invalid_request')
            self._conversation.confirm(body['project_sha256'])
            self._revision = self._conversation.revision
        return self._project()

    def prepare_application(self, run_id, digest):
        with self._lock:
            if self._conversation is not None:
                self._conversation.prepare_application(run_id, digest)
                self._revision = self._conversation.revision

    def record_application(self, receipt):
        with self._lock:
            if self._conversation is not None:
                self._conversation.note_application(receipt)

    def _persist(self):
        if getattr(self, '_conversation', None) is None:
            return super()._persist()
        operation = self._operation
        if operation is None or operation['status'] != 'running':
            return
        if any(turn['run_id'] == operation['id'] for item in self._conversation.document['conversations']
               for turn in item['turns']):
            return
        source = copy.deepcopy(self._selected_project)
        if source:
            for entry in source['files']:
                entry.pop('text')
        try:
            self._conversation.prepare(operation['id'], self._revision,
                                       self._messages[-1]['text'], self._conversation_intent, source)
        except Exception:
            self._mark('error', 'state_unavailable')
            raise

    @staticmethod
    def _empty_task(timeout):
        return {'version': 1, 'decision_mode': 'synthetic', 'workspace': 'synthetic-project',
                'run_id': None, 'steps': [], 'artifact': None, 'error': None,
                'budget': {**TASK_LIMITS, 'decisions': 0, 'tools': 0, 'read_bytes': 0,
                           'total_decisions': 0, 'total_tools': 0, 'total_read_bytes': 0,
                           'model_dispatches': 0, 'model_cost_nano': 0,
                           'deadline_seconds': timeout}}

    def _project(self):
        state = super()._project()
        self._task['budget'].update({'total_' + key: value for key, value in self._totals.items()})
        task = copy.deepcopy(self._task)
        if self._model_status is not None:
            task.update(version=2, decision_mode='synthetic_protocol',
                        model=copy.deepcopy(self._model_status()),
                        rejection=copy.deepcopy(self._task.get('rejection')))
            task['budget'].update(model_dispatches=task['model']['calls'],
                model_cost_nano=task['model'].get('model_accounted_nano_usd', task['model']['accounted_nano_usd']))
        # The decider receives full observations; HTTP only needs bounded previews.
        for step in task['steps']:
            result = step['result']
            if step['tool'] == 'read' and result['status'] == 'ok':
                result['text'] = result['text'].encode('utf-8')[:512].decode('utf-8', errors='ignore')
                result['preview'] = True
            elif step['tool'] == 'search' and result['status'] == 'ok':
                result['matches'] = [{**match, 'text': match['text'].encode('utf-8')[:128]
                                      .decode('utf-8', errors='ignore')}
                                     for match in result['matches'][:4]]
                result['preview'] = True
        state.update(mode='agent_demo', prompts=list(PROMPTS), task=task)
        if self._web_search_factory is not None:
            state.update(web_search_available=True, web_search_state=copy.deepcopy(self._search_state))
            if self._web_search_provider != 'bing':
                state['web_search_provider'] = self._web_search_provider
        if self._selected_project is not None:
            state['project'] = copy.deepcopy(self._selected_project)
        if self._followup is not None:
            state['followup'] = self._followup.project()
        if self._web is not None:
            state['web'] = self._web.project()
        if self._conversation is not None:
            state['conversation'] = self._conversation.project(can_refresh=self._project_reader is not None)
            state['limits']['max_turns'] = self._turn_limit
            state['limits']['max_message_chars'] = (self._ordinary_capacity.message_chars
                if self._ordinary_capacity is not None else MAX_GOAL_CHARS)
            state['limits']['max_answer_chars'] = (self._ordinary_capacity.answer_chars
                if self._ordinary_capacity is not None else MAX_ANSWER_CHARS)
        if self._conversation_capacity is not None:
            state['send_budget'] = dict(requests_used=getattr(self, '_process_requests', len(self._seen)),
                                        max_requests=24)
        return self.finalize_projection(state)

    def finalize_projection(self, state, *, resources=()):
        """Project owned stop/worker facts and retain the existing byte policy."""
        with self._lock:
            from pacttrail.application.run_feedback import project_run_feedback
            feedback = project_run_feedback(state, worker_busy=self._busy(),
                stop_accepted=self._stop_accepted_run == (state.get('operation') or {}).get('id'),
                resources=resources)
            if feedback is not None:
                state['run_feedback'] = feedback
            return self._limit_web_projection(state)

    @property
    def response_byte_limit(self):
        return MAX_RESPONSE_BYTES if self._conversation_capacity is not None else super().response_byte_limit

    def _limit_web_projection(self, state):
        if (_bytes(state) > state['limits']['max_response_bytes']
                and self._web is not None and state.get('web', {}).get('preview') is not None):
            self._web.reject_preview()
            state['web'] = self._web.project()
        if _bytes(state) > state['limits']['max_response_bytes']:
            raise chat_error('response_limit')
        return state

    def _retire_decider(self):
        # Cleanup can re-enter reset/clear. Notify with the identity captured
        # before cleanup, never a replacement run's mutable operation dict.
        retired = self.control_state().run
        searcher, self._searcher = self._searcher, None
        decider = self._decider if not self._decider_closed else None
        if decider is not None:
            self._decider_closed = True
        if searcher is not None:
            try:
                searcher.close()
            except Exception:
                pass
        if decider is not None:
            try:
                close = getattr(decider, 'close', None)
                if callable(close):
                    close()
            except Exception:
                pass
        if self._on_retired is not None:
            self._on_retired(retired)

    def _cancel_work(self):
        if self._search_state is not None and self._search_state['status'] == 'searching':
            self._search_state.update(status='cancelled', error='cancelled')
        if self._operation is not None and self._operation['status'] == 'running':
            self._mark('stopped')
        super()._cancel_work()
        self._retire_decider()
        self._checkpoint_record(strict=False)

    def _checkpoint_record(self, record=None, *, strict=True):
        record = self._recording if record is None else record
        if record is None or record.failed:
            if strict and self._record_failed:
                raise chat_error('state_unavailable')
            return
        try:
            # A completed adapter can re-enter reset before its bytes are charged.
            # Keep that retired run's facts consistent with the process ledger too.
            record.task['budget'].update({'total_' + key: value for key, value in self._totals.items()})
            record.checkpoint()
            if self._conversation is not None and record.operation['status'] != 'running':
                artifact = None
                if record.operation['status'] == 'complete' and record.task['artifact'] is not None:
                    saved = self._record_store.report(record.task['run_id'])
                    artifact = {key: saved[key] for key in ('name', 'sha256', 'bytes', 'text')}
                answer = next((item['text'] for item in reversed(self._messages)
                               if item['role'] == 'assistant' and item['operation_id'] == record.task['run_id']), None)
                self._conversation.finish(record.task['run_id'], record.operation['status'],
                    record.operation['error'], answer, artifact,
                    web_search=next((item.get('web_search') for item in reversed(self._messages)
                        if item['role'] == 'assistant' and item['operation_id'] == record.task['run_id']), None))
        except Exception:
            self._record_failed = True
            record.failed = True
            # Keep the last durable checkpoint. Never dispatch after failed storage.
            if record.task is self._task:
                if self._conversation is not None:
                    if (self._messages and self._messages[-1]['role'] == 'assistant'
                            and self._messages[-1]['operation_id'] == record.task['run_id']):
                        self._messages.pop()
                    if self._task['steps'] and self._task['steps'][-1]['kind'] == 'finish':
                        self._task['steps'].pop()
                self._task['error'] = 'state_unavailable'
                if self._operation is not None:
                    self._mark('error', 'state_unavailable')
                self._cancel.set()
                self._retire_decider()
            if strict:
                raise chat_error('state_unavailable') from None

    def _verify_artifact(self):
        if self._task['artifact'] is not None:
            try:
                artifact = self._files.verify_artifact(self._task['run_id'])
                if artifact != self._task['artifact']:
                    raise ValueError()
            except Exception:
                self._task.update(artifact=None, error='artifact_conflict')
                self._task.pop('rejection', None)
                if self._operation is not None:
                    self._mark('error', 'state_unavailable')
                self._cancel_work()

    def read_binding(self):
        """Read current identity and worker occupancy without advancing a run."""
        with self._lock:
            if self._closed:
                raise chat_error('closed')
            return dict(conversation_id=self._conversation_id, revision=self._revision, busy=self._busy())

    def status(self):
        with self._lock:
            self._expire()
            self._verify_artifact()
            self._expire()
            return self._project()

    def action(self, path, body):
        with self._lock:
            if path == '/preview':
                from pacttrail.application.send_preview import preview_send
                return preview_send(self, body)
            if path == '/conversation' and type(body) is dict and body.get('action') in ('read_archive', 'read_history', 'summary_candidate'):
                if self._closed:
                    raise chat_error('closed')
                return self._conversation_action(body)
            self._expire()
            if self._closed:
                raise chat_error('closed')
            if self._conversation is not None and self._conversation.failed:
                raise chat_error('history_unavailable')
            if path == '/conversation':
                return self._conversation_action(body)
            if self._conversation is not None and path in ('/material', '/web', '/followup'):
                raise chat_error('invalid_request')
            if self._conversation is not None and path == '/reset':
                if (type(body) is not dict or set(body) != {'conversation_id', 'revision', 'provider_id'}
                        or body['provider_id'] != 'offline'):
                    raise chat_error('invalid_request')
                self._bound(body)
                if len(self._conversation.document['conversations']) >= 8:
                    raise chat_error('history_limit')
                self._cancel_work()
                import secrets
                self._conversation.new(secrets.token_hex(16))
                self._restore_conversation()
                return self._project()
            if path not in ('/send', '/stop', '/reset', '/material', '/project', '/followup', '/web'):
                raise chat_error('invalid_request')
            if path == '/web':
                return self._web_action(body)
            if self._web is not None:
                if path == '/reset':
                    if type(body) is not dict or set(body) != {'conversation_id', 'revision', 'provider_id'} or body['provider_id'] != 'offline':
                        raise chat_error('invalid_request')
                    self._bound(body)
                    self._web.clear()
                elif path in ('/send', '/material', '/project', '/followup') and self._web.blocks_task():
                    raise chat_error('busy')
            if path == '/followup':
                return self._followup_action(body)
            if (path in ('/material', '/project') and self._followup is not None
                    and self._followup.selected is not None):
                raise chat_error('material_exists')
            if path == '/project':
                return self._project_action(body)
            if (path == '/material' and self._selected_project is not None
                    and type(body) is dict and body.get('action') == 'attach'):
                raise chat_error('material_exists')
            if path == '/reset' and type(body) is dict and body.get('provider_id') != 'offline':
                raise chat_error('invalid_request')
            if path == '/send' and type(body) is dict:
                # Validate the additional scope before the base can start a worker.
                body = dict(body)
                summary_binding = body.get('context_plan_sha256')
                history_binding = self._history_references.check_send(self._conversation, body)
                has_reference = 'artifact_reference_sha256' in body
                reference_binding = body.pop('artifact_reference_sha256', None)
                if has_reference and (type(reference_binding) is not str
                        or re.fullmatch('[a-f0-9]{64}', reference_binding) is None):
                    raise chat_error('invalid_request')
                reference = self._conversation.restore().get('artifact_reference') if self._conversation else None
                reference_request = body.get('request_id')
                if (type(reference_request) is str and reference_request in self._artifact_reference_bindings
                        and self._artifact_reference_bindings[reference_request] != reference_binding):
                    raise chat_error('request_conflict')
                if reference_binding != (reference['artifact']['sha256'] if reference else None):
                    raise chat_error('stale_request')
                if reference is not None and reference['state'] != 'confirmed':
                    raise chat_error('stale_request')
                has_intent = 'task_intent' in body
                intent = body.pop('task_intent', None)
                conversation_intent = intent
                search_requested = body.pop('web_search', False)
                if (type(search_requested) is not bool or search_requested and (
                        self._web_search_factory is None or self._conversation is None or intent != 'answer')):
                    raise chat_error('invalid_request')
                search_request_id = body.get('request_id')
                if (type(search_request_id) is str and self._search_bindings.get(search_request_id) is not None
                        and self._search_bindings[search_request_id] != search_requested):
                    raise chat_error('request_conflict')
                has_creation = 'creation_path' in body
                creation_path = body.pop('creation_path', None)
                if has_creation:
                    if (self._conversation is None or intent != 'change_draft'
                            or self._project_reader_root is None or self._selected_project is None):
                        raise chat_error('invalid_request')
                    from pacttrail.domain.change_draft import validate_creation_path
                    try:
                        descriptor = copy.deepcopy(self._selected_project)
                        for entry in descriptor['files']:
                            entry.pop('text')
                        creation_path = validate_creation_path(creation_path, descriptor)
                    except DecisionError:
                        raise chat_error('invalid_request') from None
                if self._conversation is not None:
                    if type(intent) is not str or intent not in ('answer', 'report', 'change_draft'):
                        raise chat_error('invalid_request')
                    if self._conversation.restore()['scope_state'] == 'review_required':
                        raise chat_error('stale_request')
                    intent = None if intent == 'report' else intent
                elif has_intent and (type(intent) is not str or intent != 'change_draft'):
                    raise chat_error('invalid_request')
                request_id = body.get('request_id')
                if (self._conversation is not None and intent == 'change_draft' and not has_creation
                        and type(body.get('message')) is str and body['message'].startswith(DRAFT_FAILURE_PREFIX)):
                    if type(request_id) is str and request_id in self._creation_bindings:
                        creation_path = self._creation_bindings[request_id]
                    else:
                        try:
                            descriptor = copy.deepcopy(self._selected_project)
                            for entry in descriptor['files']:
                                entry.pop('text')
                            prior = failure_draft(body['message'], self._conversation.context(), descriptor)
                            creation_path = prior['changes'][0]['path'] if prior['version'] == 2 else None
                            if creation_path is not None and self._project_reader_root is None:
                                raise DecisionError()
                        except Exception:
                            raise chat_error('stale_request') from None
                if (type(request_id) is str and request_id in self._creation_bindings
                        and self._creation_bindings[request_id] != creation_path):
                    raise chat_error('request_conflict')
                if (type(request_id) is str and request_id in self._intent_bindings
                        and self._intent_bindings[request_id] != conversation_intent):
                    raise chat_error('request_conflict')
                if intent == 'change_draft' and (
                        self._selected_project is None or self._selected_project['version'] != 1
                        or self._followup is not None and self._followup.selected is not None):
                    raise chat_error('invalid_request')
                if 'followup_sha256' in body:
                    if type(body['followup_sha256']) is not str or re.fullmatch('[a-f0-9]{64}', body['followup_sha256']) is None:
                        raise chat_error('invalid_request')
                followup_binding = body.pop('followup_sha256', None)
                request_id = body.get('request_id')
                duplicate = type(request_id) is str and request_id in self._seen
                if type(request_id) is str and request_id in self._followup_bindings:
                    if self._followup_bindings[request_id] != followup_binding:
                        raise chat_error('request_conflict')
                selected = self._followup is not None and self._followup.selected is not None
                if (selected and followup_binding != self._followup.selected['binding_sha256']
                        or not selected and followup_binding is not None):
                    raise chat_error('stale_request')
                bindings = []
                for key, snapshot in (('material_sha256', self._material),
                                      ('project_sha256', self._selected_project)):
                    if key in body and (type(body[key]) is not str or snapshot is None):
                        raise chat_error('invalid_request')
                    value = body.pop(key, None)
                    if value != (snapshot['sha256'] if snapshot is not None else None):
                        raise chat_error('stale_request')
                    bindings.append(value)
                binding = tuple(bindings)
                if (type(body.get('request_id')) is str and body['request_id'] in self._material_bindings
                        and self._material_bindings[body['request_id']] != binding):
                    raise chat_error('request_conflict')
                if selected and not duplicate:
                    # Check request identity before any parent re-read; a new send
                    # then validates the exact selected bytes before worker setup.
                    if (set(body) != {'conversation_id', 'revision', 'request_id', 'message'}
                            or not _identifier(request_id)
                            or not valid_text(body['message'], self._max_message_chars())):
                        raise chat_error('invalid_request')
                    self._bound(body)
                    references = self._followup.selected.get('rounds', [self._followup.selected['parent']])
                    if request_id in {item['run_id'] for item in references}:
                        raise chat_error('request_conflict')
                    if self._busy():
                        raise chat_error('busy')
                    try:
                        self._followup.confirm(followup_binding)
                    except Exception:
                        self._material = self._selected_project = None
                        self._revision += 1
                        raise
                self._send_intent = intent
                self._send_web_search = search_requested
                self._send_creation_path = creation_path
                self._conversation_intent = conversation_intent
                try:
                    if reference is not None and not duplicate:
                        # Exact identity/text is checked again only at an explicit
                        # new send. Historical viewing cannot create an executor.
                        self._bound(body)
                        if self._busy():
                            raise chat_error('busy')
                        if (set(body) != {'conversation_id', 'revision', 'request_id', 'message'}
                                or not _identifier(request_id)
                                or not valid_text(body['message'], self._max_message_chars())):
                            raise chat_error('invalid_request')
                        if self._artifact_reference_reader is None:
                            raise chat_error('stale_request')
                        current = self._artifact_reference_reader(reference['task_id'], reference['run_id'])
                        current['state'] = reference['state']
                        if current != reference:
                            raise chat_error('stale_request')
                    super().action(path, body)
                finally:
                    self._send_intent = None
                    self._send_web_search = False
                    self._send_creation_path = None
                    self._conversation_intent = None
                self._material_bindings[body['request_id']] = binding
                self._followup_bindings[body['request_id']] = followup_binding
                self._intent_bindings[body['request_id']] = conversation_intent
                self._creation_bindings[body['request_id']] = creation_path
                self._search_bindings[body['request_id']] = search_requested
                self._artifact_reference_bindings[body['request_id']] = reference_binding
                self._history_references.accepted(body['request_id'], history_binding, summary_binding)
            else:
                super().action(path, body)
            if path == '/stop':
                self._stop_accepted_run = body['request_id']
            if path == '/reset':
                self._selected_project = None
                if self._followup is not None:
                    self._followup.clear()
                self._task = self._empty_task(self._completion_timeout)
                return self._project()
            self._verify_artifact()
            self._expire()
            return self._project()

    def _web_action(self, body):
        fields = {'fetch': {'fetch_id', 'urls'}, 'cancel': {'fetch_id'},
                  'attach': {'fetch_id', 'project_sha256'}, 'clear': set()}
        if (self._web is None or type(body) is not dict or type(body.get('action')) is not str
                or body['action'] not in fields
                or set(body) != {'conversation_id', 'revision', 'action'} | fields[body['action']]):
            raise chat_error('invalid_request')
        self._bound(body)
        if self._busy():
            raise chat_error('busy')
        action = body['action']
        if action in ('fetch', 'attach') and (self._material is not None or self._selected_project is not None
                or self._followup is not None and self._followup.selected is not None):
            raise chat_error('material_exists')
        if action == 'fetch':
            self._web.fetch(body['fetch_id'], body['urls'])
        elif action == 'cancel':
            self._web.cancel(body['fetch_id'])
        elif action == 'attach':
            self._selected_project = self._web.attach(body['fetch_id'], body['project_sha256'])
        else:
            self._web.clear()
        self._revision += 1
        return self._project()

    def close(self):
        with self._lock:
            if self._web is not None:
                self._web.close()
            super().close()
            if self._conversation is not None:
                self._conversation.close()

    def _followup_action(self, body):
        import re
        if (self._followup is None or type(body) is not dict
                or type(body.get('action')) is not str or body['action'] not in ('list', 'select', 'clear')
                or set(body) != {'conversation_id', 'revision', 'action'}
                | (({'run_id'} | ({'round_count'} if 'round_count' in body else set()))
                   if body['action'] == 'select' else set())):
            raise chat_error('invalid_request')
        self._bound(body)
        if self._busy():
            raise chat_error('busy')
        if body['action'] == 'select':
            if type(body['run_id']) is not str or re.fullmatch('[a-f0-9]{32}', body['run_id']) is None:
                raise chat_error('invalid_request')
            if 'round_count' in body and (type(body['round_count']) is not int or not 1 <= body['round_count'] <= 3):
                raise chat_error('invalid_request')
            if (self._followup.selected is None
                    and (self._material is not None or self._selected_project is not None)):
                raise chat_error('material_exists')
        if self._followup.selected is not None:
            self._material = self._selected_project = None
        if body['action'] == 'select':
            self._followup.select(body['run_id'], body.get('round_count'))
            if self._followup.bundle is not None:
                snapshot = copy.deepcopy(self._followup.bundle['snapshot'])
                if snapshot['kind'] == 'material':
                    self._material = snapshot['value']
                else:
                    self._selected_project = snapshot['value']
        elif body['action'] == 'list':
            self._followup.refresh()
        else:
            self._followup.clear()
        self._revision += 1
        try:
            return self._project()
        except Exception:
            self._followup.fail('response_limit')
            self._material = self._selected_project = None
            self._followup.records = []
            return self._project()

    def _project_action(self, body):
        from pacttrail.application.chat_project import from_upload
        fields = {'attach': {'name', 'files'}, 'remove': set()}
        if (type(body) is not dict or type(body.get('action')) is not str or body['action'] not in fields
                or set(body) != {'conversation_id', 'revision', 'action'} | fields[body['action']]):
            raise chat_error('invalid_request')
        self._bound(body)
        if self._busy():
            raise chat_error('busy')
        if body['action'] == 'attach':
            if self._material is not None or self._selected_project is not None:
                raise chat_error('material_exists')
            snapshot = from_upload(body['name'], body['files'])
            projected = self._project()
            projected.update(project=snapshot, revision=self._revision + 1)
            if _bytes(projected) > projected['limits']['max_response_bytes'] - 1024:
                raise chat_error('response_limit')
        else:
            if self._selected_project is None:
                raise chat_error('material_unavailable')
            snapshot = None
        if self._conversation is not None:
            self._conversation.set_scope(snapshot)
            self._revision = self._conversation.revision
        else:
            self._revision += 1
        self._selected_project = snapshot
        return self._project()

    def _max_message_chars(self):
        if self._conversation is not None and self._ordinary_capacity is not None:
            return (MAX_GOAL_CHARS if self._conversation_intent in ('report', 'change_draft')
                    else self._ordinary_capacity.message_chars)
        return MAX_GOAL_CHARS if self._conversation is not None else super()._max_message_chars()

    def _max_summary_chars(self):
        if self._conversation is not None and self._ordinary_capacity is not None and self._task.get('intent') == 'answer':
            return self._ordinary_capacity.answer_chars
        return MAX_ANSWER_CHARS if self._conversation is not None else 1024

    def _send(self, body):
        if self._record_failed:
            raise chat_error('state_unavailable')
        # Base validation/deduplication happens before a worker can acquire lock.
        duplicate = body['request_id'] in self._seen
        if self._conversation is not None and not duplicate:
            self._conversation.context()  # Refuse an oversized selected envelope before durable mutation.
        if not duplicate and self._busy():
            raise chat_error('busy')
        if not duplicate and self._decide_override is not None and self._override_used:
            raise chat_error('provider_unavailable')
        previous_operation = self._operation
        try:
            state = super()._send(body)
        except Exception:
            # Base validation preserves the previous view. Only a newly accepted
            # request whose worker failed to start receives its own empty task;
            # otherwise a previous report would appear to belong to the new goal.
            if (not duplicate and self._operation is not previous_operation
                    and self._operation is not None and self._operation['id'] == body['request_id']
                    and self._operation['status'] == 'error'
                    and (self._conversation_id, self._revision)
                    == (body['conversation_id'], body['revision'])):
                self._task = self._empty_task(self._completion_timeout)
                self._task.update(run_id=body['request_id'], error=self._operation['error'])
                self._set_record_source()
                self._start_record(body['message'])
            raise
        if not duplicate:
            self._task = self._empty_task(self._completion_timeout)
            self._task_creation_path = self._send_creation_path
            self._task['run_id'] = body['request_id']
            self._decider = None
            self._override_used = self._decide_override is not None
            self._decider_closed = False
            self._search_state = None
            view = (self._conversation_id, self._revision)
            operation, cancel = self._operation, self._cancel
            try:
                self._set_record_source()
                self._start_record(body['message'])
                if self._send_web_search:
                    self._searcher = self._new_searcher()
                    self._search_state = dict(run_id=body['request_id'], status='searching', error=None)
                self._run_files = self._files
                source = None
                if self._material is not None:
                    self._run_files = self._files.bind_material(body['request_id'], self._material)
                    source = copy.deepcopy(self._run_files.source)
                    self._task.update(workspace='selected-text', source=source)
                elif self._selected_project is not None:
                    self._run_files = self._files.bind_project(body['request_id'], self._selected_project)
                    source = copy.deepcopy(self._run_files.source)
                    self._task.update(workspace='selected-project', source=source)
                self._checkpoint_record()
                if self._decision_factory is not None:
                    self._decider = self._decision_factory(body['request_id'], self._deadline, self._cancel)
                    if (not callable(self._decider) or
                            self._decider.completion_timeout != self._completion_timeout):
                        raise ValueError('deadline')
                else:
                    self._decider = self._decide_override if self._decide_override is not None else SyntheticTaskDecision()
                if source is not None:
                    self._decider.set_source(copy.deepcopy(source))
                if self._task.get('intent') in ('answer', 'change_draft'):
                    self._decider.set_intent(self._task['intent'])
                if self._task_creation_path is not None:
                    self._decider.set_creation(self._task_creation_path)
                if self._conversation is not None:
                    if self._conversation_capacity is not None:
                        self._decider.set_conversation_capacity(self._conversation_capacity)
                    self._decider.set_conversation(self._conversation.context())
                    if self._task.get('intent') == 'answer' and self._ordinary_capacity is not None:
                        self._decider.set_ordinary_capacity(self._ordinary_capacity)
                if self._followup is not None and self._followup.context is not None:
                    self._decider.set_followup(copy.deepcopy(self._followup.context))
            except Exception:
                # A trusted factory can re-enter reset/clear/close before it
                # returns or raises. Fail only the view that started this run;
                # an old factory failure cannot mark a replacement task/view.
                if (view == (self._conversation_id, self._revision)
                        and self._operation is operation and operation is not None
                        and operation['id'] == body['request_id'] and operation['status'] == 'running'
                        and self._task['run_id'] == body['request_id']
                        and not self._closed and not cancel.is_set()):
                    self._task['error'] = 'provider_unavailable'
                    self._mark('error', 'provider_unavailable')
                    self._cancel_work()
                else:
                    self._retire_decider()
            state = self._project()
        return state

    def _start_record(self, goal):
        from pacttrail.application.task_recording import TaskRecording
        context = self._followup.context if self._followup is not None else None
        reference = None
        if self._conversation is not None:
            selected = self._conversation.restore().get('artifact_reference')
            if selected is not None:
                from pacttrail.domain.artifact_reference import reference_identity
                reference = reference_identity(selected)
        self._recording = TaskRecording(self._record_store, self._task, self._operation,
                                       goal, self._model_status, self._recording_mode,
                                       followup_context=context if context is not None and context['version'] == 2 else None,
                                       artifact_reference=reference)
        self._checkpoint_record()

    def _set_record_source(self):
        # Keep the accepted scope even if worker start or snapshot copying fails.
        if self._send_intent is not None:
            self._task['intent'] = self._send_intent
        if self._followup is not None and self._followup.selected is not None:
            self._task['parent'] = copy.deepcopy(self._followup.selected['parent'])
        if self._material is not None:
            self._task.update(workspace='selected-text', source={
                'kind': 'selected_text', 'file_id': 'selected.txt',
                **{key: self._material[key] for key in ('name', 'sha256', 'byte_count', 'line_count')}})
        elif self._selected_project is not None:
            selected = copy.deepcopy(self._selected_project)
            for entry in selected['files']:
                entry.pop('text')
            self._task.update(workspace='selected-project', source=selected)

    def _charge(self, counter):
        budget = self._task['budget']
        if (budget[counter] >= TASK_LIMITS['max_' + counter]
                or self._totals[counter] >= TASK_LIMITS['max_total_' + counter]):
            raise chat_error('budget_limit')
        budget[counter] += 1
        self._totals[counter] += 1

    def _remaining_reads(self):
        return min(TASK_LIMITS['max_read_bytes'] - self._task['budget']['read_bytes'],
                   TASK_LIMITS['max_total_read_bytes'] - self._totals['read_bytes'])

    def _execute(self, decision, action_id, current):
        tool, args = decision['tool'], decision['arguments']
        task = self._task
        prepared_reads = 0
        try:
            definition = tool_definition(tool)
            if definition.prepare_handler is None:
                handler = getattr(self._run_files, definition.handler)
                result = handler(*(args[field] for field in definition.argument_fields), self._remaining_reads())
            else:
                if tool == 'save_draft':
                    extra = ({'creation_path': self._task_creation_path}
                             if self._task_creation_path is not None else {})
                    proposal, prepared_reads = getattr(self._run_files, definition.prepare_handler)(
                        task['run_id'], action_id, args, self._remaining_reads(), **extra)
                else:
                    proposal = getattr(self._files, definition.prepare_handler)(task['run_id'], action_id, args['content'])
                if not current():
                    task['budget']['read_bytes'] += prepared_reads
                    self._totals['read_bytes'] += prepared_reads
                    return {'status': 'error', 'error': 'output_unavailable'}
                artifact = getattr(self._files, definition.handler)(proposal)
                # Record committed side effects before any following boundary.
                task['artifact'] = artifact
                result = {'status': 'ok', **{key: artifact[key] for key in ('name', 'sha256', 'bytes')}}
                if tool == 'save_draft':
                    result['read_bytes'] = prepared_reads
            used = result.get('read_bytes', 0)
            task['budget']['read_bytes'] += used
            self._totals['read_bytes'] += used
            return result
        except TaskFileError as error:
            # Bytes attempted before an error are charged by the adapter as well.
            used = prepared_reads + getattr(error, 'read_bytes', 0)
            task['budget']['read_bytes'] += used
            self._totals['read_bytes'] += used
            return {'status': 'error', 'error': error.code if error.code in TOOL_ERRORS else 'tool_error'}
        except Exception:
            task['budget']['read_bytes'] += prepared_reads
            self._totals['read_bytes'] += prepared_reads
            return {'status': 'error', 'error': 'tool_error'}

    def _run(self, identity, request_id, history, cancel):
        observations = []
        record = None
        try:
            # Base _send starts the worker while action still owns this lock;
            # the recording is installed before that action releases it.
            with self._lock:
                if self._recording is not None and self._recording.task['run_id'] == request_id:
                    record = self._recording
            if cancel.wait(self._delay):
                return
            with self._lock:
                if not self._current(identity, request_id, cancel):
                    return
                searcher = self._searcher
            if searcher is not None:
                from pacttrail.domain.web_search import WebSearchError, make_query, plan_query, validate_search
                try:
                    query = make_query(identity[2])
                    search_input = identity[2] if getattr(searcher, 'accepts_goal', False) is True else query
                    found = validate_search(searcher.search(search_input, cancel=cancel, deadline=self._deadline))
                    expected = (plan_query(search_input, query_limit=128 if found['provider'] == 'deepseek' else 160)
                                if found['version'] == 2 else None)
                    if found['query'] != (expected['query'] if expected else query):
                        raise WebSearchError()
                    if expected and (found['official_domains'] != list(expected['official_domains'])
                            or found['queries'] != [expected['query'], expected['secondary_query']][:len(found['queries'])]):
                        raise WebSearchError()
                except Exception as error:
                    with self._lock:
                        if not self._current(identity, request_id, cancel):
                            return
                        code = error.code if isinstance(error, WebSearchError) else 'unavailable'
                        self._search_state.update(status='error', error=code)
                    raise chat_error('source_unavailable') from None
                finally:
                    try:
                        searcher.close()
                    except Exception:
                        pass
                with self._lock:
                    if not self._current(identity, request_id, cancel):
                        return
                    self._decider.set_web_search(found)
                    self._search_state.update(status='ready')
            while True:
                with self._lock:
                    if not self._current(identity, request_id, cancel):
                        return
                    self._charge('decisions')
                    decider = self._decider
                    cost = getattr(decider, 'cost_nano', 0)
                    if not self._current(identity, request_id, cancel):
                        return
                    if self._model_status is None and (type(cost) is not int or cost != 0):
                        raise chat_error('budget_limit')
                    record.pending = {'kind': 'model', 'tool': None,
                                      'index': self._task['budget']['decisions']}
                    self._checkpoint_record(record)
                # A trusted decision callable may block; stop remains responsive.
                decision = decider(identity[2], tuple(copy.deepcopy(observations)))
                with self._lock:
                    record.pending = None
                    self._checkpoint_record(record)
                    if not self._current(identity, request_id, cancel):
                        return
                    cost = getattr(decider, 'cost_nano', 0)
                    if not self._current(identity, request_id, cancel):
                        return
                    if self._model_status is None and (type(cost) is not int or cost != 0):
                        raise chat_error('budget_limit')
                    if self._task.get('source') is not None:
                        decision = validate_decision(
                            decision, file_ids=source_file_ids(self._task['source']),
                            draft_source=self._task['source'] if self._task.get('intent') == 'change_draft' else None,
                            creation_path=self._task_creation_path, max_summary_chars=self._max_summary_chars())
                    else:
                        decision = validate_decision(decision, max_summary_chars=self._max_summary_chars())
                    index = len(self._task['steps']) + 1
                    if decision['kind'] == 'finish':
                        try:
                            artifact = self._files.verify_artifact(request_id)
                        except Exception:
                            if self._model_status is not None:
                                raise ToolResponseError('report_invalid') from None
                            raise
                        if not self._current(identity, request_id, cancel):
                            return
                        if (artifact != self._task['artifact']
                                or artifact is None and self._task.get('intent') != 'answer'):
                            if self._model_status is not None:
                                raise ToolResponseError('report_missing' if artifact is None else 'report_invalid')
                            raise DecisionError()
                        if (self._task.get('intent') == 'change_draft'
                                and getattr(decider, 'saved_acknowledgement', None) != decision['summary']):
                            decision['summary'] = (f"变更草案已保存：{artifact['name']}；"
                                                   f"SHA-256 {artifact['sha256']}；{artifact['bytes']} 字节；未应用，未执行测试。")
                        step = {'index': index, 'kind': 'finish', 'tool': None,
                                'reason': decision['summary'], 'result': {'status': 'ok'}}
                        message = {'role': 'assistant', 'text': decision['summary'],
                                   'status': 'complete', 'sources': [], 'operation_id': request_id}
                        if searcher is not None:
                            message['web_search'] = copy.deepcopy(found)
                        if self._conversation is not None:
                            projected = self._project()
                            projected['task']['steps'].append(step)
                            projected['messages'] = [*projected['messages'], message]
                            if _bytes(projected) > self.response_byte_limit - 1024:
                                raise chat_error('response_limit')
                        self._task['steps'].append(step)
                        self._messages.append(message)
                        self._operation.update(status='complete', error=None)
                        self._checkpoint_record(record)
                        self._retire_decider()
                        return
                    batch = decision['kind'] == 'tools'
                    calls = decision['calls'] if batch else [decision]
                    # A valid prefix must not execute when the whole response
                    # cannot fit either existing tool-count budget.
                    if (len(calls) > TASK_LIMITS['max_tools'] - self._task['budget']['tools']
                            or len(calls) > TASK_LIMITS['max_total_tools'] - self._totals['tools']):
                        raise chat_error('budget_limit')
                for call in calls:
                    # Release between actions so stop/clear can retire a batch.
                    # Each bounded file operation keeps the existing write lock.
                    with self._lock:
                        if not self._current(identity, request_id, cancel):
                            return
                        self._charge('tools')
                        task = self._task
                        action_id = f"{request_id}-{task['budget']['tools']}"
                        record.pending = {'kind': 'tool', 'tool': call['tool'],
                                          'index': task['budget']['tools']}
                        self._checkpoint_record(record)
                        result = self._execute(call, action_id,
                                               lambda: self._current(identity, request_id, cancel))
                        # A trusted adapter can re-enter reset. Its completed
                        # facts belong to this task, never the replacement view.
                        task['steps'].append({'index': len(task['steps']) + 1, 'kind': 'tool',
                                              'tool': call['tool'], 'reason': call['reason'], 'result': result})
                        observations.append({'action_id': action_id, 'tool': call['tool'],
                                             'arguments': call['arguments'], 'result': copy.deepcopy(result)})
                        # A failed save may have reached the filesystem. Preserve
                        # its uncertainty; the presence of a report proves nothing.
                        if call['tool'] not in ('save_report', 'save_draft') or result['status'] == 'ok':
                            record.pending = None
                        self._checkpoint_record(record)
                        if not self._current(identity, request_id, cancel):
                            return
                        if result['status'] == 'error' and (batch or call['tool'] in ('save_report', 'save_draft')):
                            raise ToolResponseError('tool_execution_failed')
        except Exception as error:
            rejection = tool_rejection(error)
            if type(error) in (SyntaxRepairUnsupported, DraftRepairUnsupported):
                code = 'invalid_response'
                reason = 'draft_repair_unsupported' if type(error) is DraftRepairUnsupported else 'syntax_repair_unsupported'
                rejection = tool_rejection(ToolResponseError(reason))
            elif isinstance(error, DecisionError):
                code = 'invalid_response'
                rejection = tool_rejection(ToolResponseError('decision_invalid'))
            else:
                codes = ('budget_limit', 'token_limit', 'token_count_unavailable', 'usage_unavailable',
                         'request_limit', 'input_limit', 'response_limit', 'invalid_response', 'invalid_request',
                         'timeout', 'source_unavailable', 'provider_auth', 'provider_rate_limit', 'provider_unavailable')
                code = next((code for code in codes if str(error) in
                             ('chat_error: ' + code, 'model_evidence_error: ' + code)), 'provider_unavailable')
                if code == 'input_limit':
                    code = 'context_limit'
            with self._lock:
                if self._current(identity, request_id, cancel):
                    self._task['error'] = code
                    if self._model_status is not None:
                        self._task['rejection'] = rejection
                        if rejection is not None and rejection['stage'] == 'consumer' and rejection['reason'] in (
                                'report_missing', 'report_invalid'):
                            # Hide only the unverifiable view. Keep the file and
                            # original terminal reason through later status/clear.
                            self._task['artifact'] = None
                    self._mark('error', code)
                    self._cancel_work()
        finally:
            with self._lock:
                self._retire_decider()
                if record is not None:
                    if record.pending is not None and record.pending['kind'] == 'model':
                        record.pending = None
                    self._checkpoint_record(record, strict=False)
