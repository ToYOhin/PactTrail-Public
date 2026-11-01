"""Plain-text offline consumer; no evidence reads, model or transport."""
import inspect
import math
from types import MappingProxyType

from pacttrail.domain.runs import PactTrailError


PROMPTS = (
    '解释什么是迭代开发。', '把上一句改写得简洁一些。', '继续上一个话题。',
    '写一句简短的欢迎语。', '把“你好”翻译成英文。', '总结目前的讨论。', '演示普通文本输入。',
)
FORMAT = 'pacttrail.ordinary-offline-history'
MAX_ANSWER_CHARS = 4096
MAX_ANSWER_BYTES = 16384


def check_completion_timeout(callback, timeout, *, owned):
    """Bind trusted ordinary composition to the session's result-validity limit."""
    missing = object()
    try:
        # Static lookup distinguishes an absent declaration from a getter that
        # raises AttributeError. Do not discover or invoke a cleanup method.
        if inspect.getattr_static(callback, 'completion_timeout', missing) is missing:
            value = None if owned else 5
        else:
            value = callback.completion_timeout
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not 0 < value <= 60 or value != timeout):
            raise ValueError('timeout declaration')
    except Exception:
        raise PactTrailError('chat_error: invalid_request') from None


def valid_text(value, limit):
    # Include BOM in blank detection, matching the browser's Unicode whitespace.
    return (type(value) is str and 0 < len(value) <= limit
            and any(not c.isspace() and c != '\ufeff' for c in value)
            and not any((ord(c) < 32 and c not in '\n\t') or ord(c) == 127
                        or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def synthetic_reply(history, message):
    """A literal echo/context fixture, not a claimed answer to the question."""
    previous = history[-2]['content'][:80] if history else '无'
    return (f'离线模拟响应（固定模板，不是真实 AI 回答）。\n'
            f'收到：{message}\n'
            f'已带入 {len(history) // 2} 轮成功上下文；上一条问题：{previous}\n'
            '此演示只验证普通文本与上下文传递，不执行问题中的指令，也不生成材料来源。')


def complete_text(callback, history, message):
    # The callback gets detached immutable pairs, never mutable session state.
    frozen = tuple(MappingProxyType(dict(pair)) for pair in history)
    return validate_answer(callback(frozen, message))


def validate_answer(answer):
    """Shared by the consumer and protocol settlement before accepting success."""
    if type(answer) is str and len(answer) > MAX_ANSWER_CHARS:
        raise PactTrailError('chat_error: response_limit')
    if not valid_text(answer, MAX_ANSWER_CHARS):
        raise PactTrailError('chat_error: invalid_response')
    if len(answer.encode('utf-8')) > MAX_ANSWER_BYTES:
        raise PactTrailError('chat_error: response_limit')
    return answer
