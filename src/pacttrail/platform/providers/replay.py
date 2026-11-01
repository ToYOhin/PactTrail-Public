"""Bounded in-memory replay for offline tests; deliberately unwired."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import unicodedata

from pacttrail.domain.runs import PactTrailError


_REQUEST_VERSION = "pacttrail.replay-request.v1"
_RESULT_VERSION = "pacttrail.replay-result.v1"
_ERROR_VERSION = "pacttrail.replay-error.v1"
_TURN_VERSION = "pacttrail.replay-turn.v1"
_FIXTURE_VERSION = "pacttrail.replay-fixture.v1"
_ID = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_MISSING = object()
_RAW_LIMIT = 262_144


def _error(reason: str) -> PactTrailError:
    return PactTrailError(f"replay_model_provider_error: {reason}")


def _text(value: object, characters: int, byte_limit: int, reason: str) -> str:
    if type(value) is not str or not value or len(value) > characters:
        raise _error(reason)
    try:
        if len(value.encode("utf-8")) > byte_limit:
            raise _error(reason)
        if unicodedata.normalize("NFC", value) != value:
            raise _error(reason)
        for character in value:
            codepoint = ord(character)
            if (
                unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                or 0xFDD0 <= codepoint <= 0xFDEF
                or codepoint & 0xFFFF in {0xFFFE, 0xFFFF}
            ):
                raise _error(reason)
    except UnicodeError:
        raise _error(reason) from None
    return value


def _literal(value: object, expected: str, reason: str) -> None:
    if type(value) is not str or value != expected:
        raise _error(reason)


def _scenario(value: object, reason: str) -> str:
    normalized = _text(value, 64, 64, reason)
    if _ID.fullmatch(normalized) is None:
        raise _error(reason)
    return normalized


def _mapping(value: object, keys: tuple[str, ...], reason: str) -> dict:
    if type(value) is not dict or len(value) != len(keys):
        raise _error(reason)
    if any(type(key) is not str for key in value) or set(value) != set(keys):
        raise _error(reason)
    return value


def _sequence(value: object, maximum: int, reason: str) -> list | tuple:
    if type(value) not in (list, tuple) or not 1 <= len(value) <= maximum:
        raise _error(reason)
    return value


def _tree(value: object, reason: str) -> None:
    """Bound a decoded JSON tree without invoking caller-defined callbacks."""
    nodes = 0
    characters = 0
    pending = [(value, 0, frozenset())]
    while pending:
        item, depth, ancestors = pending.pop()
        nodes += 1
        if nodes > 8_192:
            raise _error(reason)
        kind = type(item)
        if kind in (dict, list):
            if depth >= 16 or len(item) > 32 or id(item) in ancestors:
                raise _error(reason)
            lineage = ancestors | {id(item)}
            if kind is dict:
                for key, child in item.items():
                    _text(key, 64, 192, reason)
                    characters += len(key)
                    pending.append((child, depth + 1, lineage))
            else:
                pending.extend((child, depth + 1, lineage) for child in item)
        elif kind is str:
            _text(item, 131_072, 524_288, reason)
            characters += len(item)
        elif kind is not int or not 0 <= item <= 7:
            raise _error(reason)
        if characters > 131_072:
            raise _error(reason)


@dataclass(frozen=True, slots=True)
class ReplayMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        reason = "invalid fixture"
        if type(self.role) is not str or self.role not in {"user", "assistant"}:
            raise _error(reason)
        _text(self.content, 4_096, 12_288, reason)

    @classmethod
    def from_dict(cls, value: object) -> ReplayMessage:
        try:
            value = _mapping(value, ("role", "content"), "invalid fixture")
            return ReplayMessage(value["role"], value["content"])
        except Exception:
            raise _error("invalid fixture") from None

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def _copy_message(value: object) -> ReplayMessage:
    if type(value) is not ReplayMessage:
        raise _error("invalid fixture")
    return ReplayMessage(value.role, value.content)


@dataclass(frozen=True, slots=True)
class ReplayModelRequest:
    schema_version: str
    scenario_id: str
    turn_index: int
    messages: tuple[ReplayMessage, ...]

    def __post_init__(self) -> None:
        try:
            _literal(self.schema_version, _REQUEST_VERSION, "invalid request")
            _scenario(self.scenario_id, "invalid request")
            if type(self.turn_index) is not int or not 0 <= self.turn_index <= 7:
                raise _error("invalid request")
            source = _sequence(self.messages, 15, "invalid request")
            if len(source) != 2 * self.turn_index + 1:
                raise _error("invalid request")
            messages = tuple(_copy_message(item) for item in source)
            if any(
                item.role != ("user" if index % 2 == 0 else "assistant")
                for index, item in enumerate(messages)
            ):
                raise _error("invalid request")
            object.__setattr__(self, "messages", messages)
        except Exception:
            raise _error("invalid request") from None

    @classmethod
    def from_dict(cls, value: object) -> ReplayModelRequest:
        try:
            value = _mapping(
                value, ("schema_version", "scenario_id", "turn_index", "messages"),
                "invalid request",
            )
            _tree(value, "invalid request")
            if type(value["messages"]) is not list:
                raise _error("invalid request")
            return ReplayModelRequest(
                value["schema_version"], value["scenario_id"], value["turn_index"],
                tuple(ReplayMessage.from_dict(item) for item in value["messages"]),
            )
        except Exception:
            raise _error("invalid request") from None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "turn_index": self.turn_index,
            "messages": [item.to_dict() for item in self.messages],
        }


def _copy_request(value: object) -> ReplayModelRequest:
    try:
        if type(value) is not ReplayModelRequest:
            raise _error("invalid request")
        return ReplayModelRequest(
            value.schema_version, value.scenario_id, value.turn_index, value.messages,
        )
    except Exception:
        raise _error("invalid request") from None


@dataclass(frozen=True, slots=True)
class ReplayModelResult:
    schema_version: str
    text: str
    finish_reason: str

    def __post_init__(self) -> None:
        _literal(self.schema_version, _RESULT_VERSION, "invalid result")
        _text(self.text, 8_192, 24_576, "invalid result")
        _literal(self.finish_reason, "stop", "invalid result")

    @classmethod
    def from_dict(cls, value: object) -> ReplayModelResult:
        try:
            value = _mapping(
                value, ("schema_version", "text", "finish_reason"), "invalid result",
            )
            return ReplayModelResult(
                value["schema_version"], value["text"], value["finish_reason"],
            )
        except Exception:
            raise _error("invalid result") from None

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "text": self.text,
            "finish_reason": self.finish_reason,
        }


def _copy_result(value: object) -> ReplayModelResult:
    try:
        if type(value) is not ReplayModelResult:
            raise _error("invalid result")
        return ReplayModelResult(value.schema_version, value.text, value.finish_reason)
    except Exception:
        raise _error("invalid result") from None


@dataclass(frozen=True, slots=True)
class ReplayProviderError:
    schema_version: str
    code: str
    message: str

    def __post_init__(self) -> None:
        _literal(self.schema_version, _ERROR_VERSION, "invalid error")
        _literal(self.code, "recorded-failure", "invalid error")
        _literal(self.message, "Synthetic recorded failure.", "invalid error")

    @classmethod
    def from_dict(cls, value: object) -> ReplayProviderError:
        try:
            value = _mapping(
                value, ("schema_version", "code", "message"), "invalid error",
            )
            return ReplayProviderError(
                value["schema_version"], value["code"], value["message"],
            )
        except Exception:
            raise _error("invalid error") from None

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "code": self.code,
            "message": self.message,
        }


def _copy_error(value: object) -> ReplayProviderError:
    try:
        if type(value) is not ReplayProviderError:
            raise _error("invalid error")
        return ReplayProviderError(value.schema_version, value.code, value.message)
    except Exception:
        raise _error("invalid error") from None


@dataclass(frozen=True, slots=True)
class ReplayTurn:
    schema_version: str
    request: ReplayModelRequest
    outcome: ReplayModelResult | ReplayProviderError

    def __post_init__(self) -> None:
        try:
            _literal(self.schema_version, _TURN_VERSION, "invalid fixture")
            request = _copy_request(self.request)
            if type(self.outcome) is ReplayModelResult:
                outcome = _copy_result(self.outcome)
            else:
                outcome = _copy_error(self.outcome)
            object.__setattr__(self, "request", request)
            object.__setattr__(self, "outcome", outcome)
        except Exception:
            raise _error("invalid fixture") from None

    @classmethod
    def from_dict(cls, value: object) -> ReplayTurn:
        try:
            value = _mapping(
                value, ("schema_version", "request", "outcome"), "invalid fixture",
            )
            _tree(value, "invalid fixture")
            outcome = _mapping(value["outcome"], ("kind", "value"), "invalid fixture")
            if type(outcome["kind"]) is not str:
                raise _error("invalid fixture")
            if outcome["kind"] == "result":
                parsed = ReplayModelResult.from_dict(outcome["value"])
            elif outcome["kind"] == "error":
                parsed = ReplayProviderError.from_dict(outcome["value"])
            else:
                raise _error("invalid fixture")
            return ReplayTurn(
                value["schema_version"], ReplayModelRequest.from_dict(value["request"]),
                parsed,
            )
        except Exception:
            raise _error("invalid fixture") from None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "request": self.request.to_dict(),
            "outcome": {
                "kind": "result" if type(self.outcome) is ReplayModelResult else "error",
                "value": self.outcome.to_dict(),
            },
        }


def _copy_turn(value: object) -> ReplayTurn:
    try:
        if type(value) is not ReplayTurn:
            raise _error("invalid fixture")
        return ReplayTurn(value.schema_version, value.request, value.outcome)
    except Exception:
        raise _error("invalid fixture") from None


def _validate_transcript(turns: tuple[ReplayTurn, ...]) -> None:
    scenarios: set[str] = set()
    previous = None
    for turn in turns:
        request = turn.request
        if previous is None or request.scenario_id != previous.request.scenario_id:
            if request.scenario_id in scenarios or request.turn_index != 0:
                raise _error("invalid fixture")
            scenarios.add(request.scenario_id)
            if len(scenarios) > 4:
                raise _error("invalid fixture")
        else:
            if (
                type(previous.outcome) is not ReplayModelResult
                or request.turn_index != previous.request.turn_index + 1
            ):
                raise _error("invalid fixture")
            prefix = previous.request.messages + (
                ReplayMessage("assistant", previous.outcome.text),
            )
            if request.messages[:-1] != prefix:
                raise _error("invalid fixture")
        previous = turn


@dataclass(frozen=True, slots=True)
class ReplayFixture:
    schema_version: str
    dataset_version: str
    data_classification: str
    turns: tuple[ReplayTurn, ...]

    def __post_init__(self) -> None:
        try:
            _literal(self.schema_version, _FIXTURE_VERSION, "invalid fixture")
            _literal(self.dataset_version, "1.0.0", "invalid fixture")
            _literal(self.data_classification, "synthetic", "invalid fixture")
            source = _sequence(self.turns, 32, "invalid fixture")
            turns = tuple(_copy_turn(item) for item in source)
            _validate_transcript(turns)
            object.__setattr__(self, "turns", turns)
            _tree(self.to_dict(), "invalid fixture")
        except Exception:
            raise _error("invalid fixture") from None

    @classmethod
    def from_dict(cls, value: object) -> ReplayFixture:
        try:
            value = _mapping(
                value,
                ("schema_version", "dataset_version", "data_classification", "turns"),
                "invalid fixture",
            )
            _tree(value, "invalid fixture")
            if type(value["turns"]) is not list:
                raise _error("invalid fixture")
            return ReplayFixture(
                value["schema_version"], value["dataset_version"],
                value["data_classification"],
                tuple(ReplayTurn.from_dict(item) for item in value["turns"]),
            )
        except Exception:
            raise _error("invalid fixture") from None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_version": self.dataset_version,
            "data_classification": self.data_classification,
            "turns": [turn.to_dict() for turn in self.turns],
        }


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    if len(pairs) > 32:
        raise _error("invalid fixture")
    result = {}
    for key, value in pairs:
        _text(key, 64, 192, "invalid fixture")
        if key in result:
            raise _error("invalid fixture")
        result[key] = value
    return result


def _integer(token: str) -> int:
    if len(token) != 1 or token not in "01234567":
        raise _error("invalid fixture")
    return int(token)


def _reject_number(token: str) -> None:
    raise _error("invalid fixture")


def parse_replay_fixture(raw: object) -> ReplayFixture:
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= _RAW_LIMIT:
            raise _error("invalid fixture")
        lines = raw.split(b"\n")
        if len(lines) > 4_096 or any(len(line) > 131_072 for line in lines):
            raise _error("invalid fixture")
        if raw.startswith(b"\xef\xbb\xbf"):
            raise _error("invalid fixture")
        depth = 0
        quoted = False
        escaped = False
        for byte in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (91, 123):
                depth += 1
                if depth > 16:
                    raise _error("invalid fixture")
            elif byte in (93, 125):
                depth -= 1
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_int=_integer, parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        return ReplayFixture.from_dict(value)
    except Exception:
        raise _error("invalid fixture") from None


def _materialize_outcome(turn: ReplayTurn) -> ReplayModelResult | ReplayProviderError:
    return turn.outcome


def _validate_outcome(
    outcome: object, expected_type: type,
) -> ReplayModelResult | ReplayProviderError:
    if expected_type is ReplayModelResult:
        return _copy_result(outcome)
    return _copy_error(outcome)


class ReplayModelProvider:
    """One instance consumes one bounded scenario, with no implicit rewind."""

    __slots__ = ("_turns", "_position", "_scenario_id")

    def __init__(
        self, fixture: object = _MISSING, scenario_id: object = _MISSING,
        *extra: object, **options: object,
    ) -> None:
        try:
            if extra or options or type(fixture) is not ReplayFixture:
                raise _error("invalid fixture")
            normalized = ReplayFixture(
                fixture.schema_version, fixture.dataset_version,
                fixture.data_classification, fixture.turns,
            )
            selected = _scenario(scenario_id, "invalid fixture")
            turns = tuple(
                turn for turn in normalized.turns if turn.request.scenario_id == selected
            )
            if not turns:
                raise _error("invalid fixture")
            self._turns = turns
            self._position = 0
            self._scenario_id = selected
        except Exception:
            raise _error("invalid fixture") from None

    def _validate_state(self) -> None:
        try:
            if (
                type(self._turns) is not tuple or not 1 <= len(self._turns) <= 8
                or type(self._position) is not int
                or not 0 <= self._position <= len(self._turns)
                or any(type(turn) is not ReplayTurn for turn in self._turns)
            ):
                raise _error("invalid fixture")
            _scenario(self._scenario_id, "invalid fixture")
        except Exception:
            raise _error("invalid fixture") from None

    @property
    def position(self) -> int:
        self._validate_state()
        return self._position

    def _lookup_next(self, request: ReplayModelRequest) -> ReplayTurn:
        turn = _copy_turn(self._turns[self._position])
        if turn.request != request:
            raise _error("request mismatch")
        return turn

    def render(
        self, request: object = _MISSING, /,
        *extra_requests: object, **options: object,
    ) -> ReplayModelResult:
        if extra_requests or options:
            raise _error("invalid request")
        normalized = _copy_request(request)
        self._validate_state()
        if self._position == len(self._turns):
            raise _error("exhausted")
        try:
            candidate = self._lookup_next(normalized)
        except PactTrailError as caught:
            if (
                type(caught) is PactTrailError and len(caught.args) == 1
                and type(caught.args[0]) is str
            ):
                if caught.args[0] == "replay_model_provider_error: request mismatch":
                    raise _error("request mismatch") from None
                if caught.args[0] == "replay_model_provider_error: invalid fixture":
                    raise _error("invalid fixture") from None
            raise _error("lookup failed") from None
        except Exception:
            raise _error("lookup failed") from None
        turn = _copy_turn(candidate)
        if (
            turn.request.scenario_id != self._scenario_id
            or turn.request.turn_index != self._position
        ):
            raise _error("invalid fixture")
        if turn.request != normalized:
            raise _error("request mismatch")
        try:
            materialized = _materialize_outcome(turn)
        except Exception:
            raise _error("lookup failed") from None
        try:
            outcome = _validate_outcome(materialized, type(turn.outcome))
        except Exception:
            reason = "invalid result" if type(turn.outcome) is ReplayModelResult else "invalid error"
            raise _error(reason) from None
        self._position += 1
        if type(outcome) is ReplayProviderError:
            raise _error("recorded failure")
        return outcome


__all__ = [
    "ReplayMessage", "ReplayModelRequest", "ReplayModelResult", "ReplayProviderError",
    "ReplayTurn", "ReplayFixture", "ReplayModelProvider", "parse_replay_fixture",
]
