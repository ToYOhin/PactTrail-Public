"""Launch-owned ordinary limits; durable reads use the fixed compatibility ceiling."""
from dataclasses import dataclass

from pacttrail.domain.tool_task import DecisionError


DEFAULT_CHARS = 2048
MAX_CHARS = 4096
DEFAULT_TURNS = 12
MAX_TURNS = 32
DEFAULT_CONTEXT_BYTES = 16 * 1024
MAX_CONTEXT_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 512 * 1024


@dataclass(frozen=True)
class OrdinaryCapacity:
    message_chars: int = DEFAULT_CHARS
    answer_chars: int = DEFAULT_CHARS

    def __post_init__(self):
        if any(type(value) is not int or not 1 <= value <= MAX_CHARS
               for value in (self.message_chars, self.answer_chars)):
            raise DecisionError()


@dataclass(frozen=True)
class ConversationCapacity:
    max_turns: int = DEFAULT_TURNS
    max_context_bytes: int = DEFAULT_CONTEXT_BYTES

    def __post_init__(self):
        if (type(self.max_turns) is not int or not 1 <= self.max_turns <= MAX_TURNS
                or type(self.max_context_bytes) is not int
                or not 1024 <= self.max_context_bytes <= MAX_CONTEXT_BYTES):
            raise DecisionError()
