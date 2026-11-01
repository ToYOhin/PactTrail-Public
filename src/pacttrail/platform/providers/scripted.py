"""Bounded, deterministic scripted rendering for offline tests.

This narrow test seam is used by ``ScriptedAgent`` through the compatibility
gate accepted by PT-RFC-0007.  It remains an offline scripted adapter, not a
complete model-provider contract or a selectable production runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from string import Formatter

from pacttrail.domain.runs import PactTrailError


_MAX_INPUT_CHARACTERS = 16_384
_MAX_INPUT_BYTES = 49_152
_MAX_OUTPUT_CHARACTERS = 65_536
_MAX_OUTPUT_BYTES = 196_608
_MAX_REPLACEMENT_FIELDS = 256
_MAX_FORMAT_SPEC_CHARACTERS = 128
_MAX_FORMAT_WIDTH_OR_PRECISION = 65_536

_REQUEST_FIELDS = frozenset({"response_template", "query"})
_QUERY_FIELD = re.compile(r"query(?:\[([0-9]+)\])*")
_QUERY_INDEX = re.compile(r"\[([0-9]+)\]")
_ASCII_DIGITS = frozenset("0123456789")
_ALIGNMENTS = frozenset("<^>")
_MISSING_REQUEST = object()


def _provider_error(reason: str) -> PactTrailError:
    return PactTrailError(f"scripted_model_provider_error: {reason}")


def _validate_input_text(value: object) -> str:
    if type(value) is not str:
        raise _provider_error("invalid request")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise _provider_error("invalid request") from None
    if len(value) > _MAX_INPUT_CHARACTERS or len(encoded) > _MAX_INPUT_BYTES:
        raise _provider_error("input exceeds limits")
    return value


def _validate_result_text(value: object) -> str:
    if type(value) is not str:
        raise _provider_error("invalid result")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise _provider_error("invalid result") from None
    if len(value) > _MAX_OUTPUT_CHARACTERS or len(encoded) > _MAX_OUTPUT_BYTES:
        raise _provider_error("output exceeds limits")
    return value


def _unsupported_template() -> PactTrailError:
    return _provider_error("unsupported template")


def _bounded_index(index_text: str, value_length: int) -> int:
    normalized = index_text.lstrip("0") or "0"
    if len(normalized) > len(str(_MAX_INPUT_CHARACTERS)):
        raise _unsupported_template()
    index = int(normalized)
    if index >= value_length:
        raise _unsupported_template()
    return index


def _resolve_query_field(field_name: str, stripped_query: str) -> str:
    if _QUERY_FIELD.fullmatch(field_name) is None:
        raise _unsupported_template()

    value = stripped_query
    for index_match in _QUERY_INDEX.finditer(field_name):
        index = _bounded_index(index_match.group(1), len(value))
        value = value[index]
    return value


def _converted_text(value: str, conversion: str | None) -> str:
    if conversion is None or conversion == "s":
        return value
    if conversion == "r":
        return repr(value)
    if conversion == "a":
        return ascii(value)
    raise _unsupported_template()


def _parse_decimal(spec: str, offset: int) -> tuple[int | None, int]:
    end = offset
    while end < len(spec) and spec[end] in _ASCII_DIGITS:
        end += 1
    if end == offset:
        return None, offset
    value = int(spec[offset:end])
    if value > _MAX_FORMAT_WIDTH_OR_PRECISION:
        raise _provider_error("output exceeds limits")
    return value, end


def _parse_format_spec(
    spec: str,
) -> tuple[int | None, int | None, str]:
    if len(spec) > _MAX_FORMAT_SPEC_CHARACTERS:
        raise _provider_error("input exceeds limits")
    if "{" in spec or "}" in spec:
        raise _unsupported_template()

    offset = 0
    fill = " "
    if len(spec) >= 2 and spec[1] in _ALIGNMENTS:
        fill = spec[0]
        offset = 2
    elif spec and spec[0] in _ALIGNMENTS:
        offset = 1

    width, offset = _parse_decimal(spec, offset)
    precision = None
    if offset < len(spec) and spec[offset] == ".":
        precision, offset = _parse_decimal(spec, offset + 1)
        if precision is None:
            raise _unsupported_template()
    if offset < len(spec) and spec[offset] == "s":
        offset += 1
    if offset != len(spec):
        raise _unsupported_template()
    return width, precision, fill


def _projected_field_length(
    value: str,
    conversion: str | None,
    format_spec: str,
) -> tuple[int, int]:
    converted = _converted_text(value, conversion)
    width, precision, fill = _parse_format_spec(format_spec)
    if precision is not None:
        converted = converted[:precision]
    content_length = len(converted)
    projected_characters = max(content_length, width or 0)
    padding = projected_characters - content_length
    projected_bytes = len(converted.encode("utf-8")) + (
        padding * len(fill.encode("utf-8"))
    )
    return projected_characters, projected_bytes


def _preflight_template(response_template: str, stripped_query: str) -> None:
    projected_characters = 0
    projected_bytes = 0
    field_count = 0
    try:
        parts = Formatter().parse(response_template)
        for literal, field_name, format_spec, conversion in parts:
            projected_characters += len(literal)
            projected_bytes += len(literal.encode("utf-8"))
            if (
                projected_characters > _MAX_OUTPUT_CHARACTERS
                or projected_bytes > _MAX_OUTPUT_BYTES
            ):
                raise _provider_error("output exceeds limits")
            if field_name is None:
                continue

            field_count += 1
            if field_count > _MAX_REPLACEMENT_FIELDS:
                raise _provider_error("input exceeds limits")
            field_value = _resolve_query_field(field_name, stripped_query)
            field_characters, field_bytes = _projected_field_length(
                field_value,
                conversion,
                format_spec,
            )
            projected_characters += field_characters
            projected_bytes += field_bytes
            if (
                projected_characters > _MAX_OUTPUT_CHARACTERS
                or projected_bytes > _MAX_OUTPUT_BYTES
            ):
                raise _provider_error("output exceeds limits")
    except PactTrailError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
        raise _unsupported_template() from None


def _render_once(response_template: str, stripped_query: str) -> str:
    return response_template.format(query=stripped_query)


@dataclass(frozen=True, slots=True)
class ScriptedModelRequest:
    """Ephemeral, strictly bounded input for the scripted test provider."""

    response_template: str
    query: str

    def __post_init__(self) -> None:
        _validate_input_text(self.response_template)
        _validate_input_text(self.query)

    @classmethod
    def from_dict(cls, value: object) -> ScriptedModelRequest:
        if type(value) is not dict:
            raise _provider_error("invalid request")
        try:
            keys = tuple(value)
        except RuntimeError:
            raise _provider_error("invalid request") from None
        if any(type(key) is not str for key in keys):
            raise _provider_error("invalid request")
        if len(keys) != len(_REQUEST_FIELDS) or frozenset(keys) != _REQUEST_FIELDS:
            raise _provider_error("invalid request")
        try:
            response_template = value["response_template"]
            query = value["query"]
        except (KeyError, RuntimeError):
            raise _provider_error("invalid request") from None
        return ScriptedModelRequest(
            response_template=response_template,
            query=query,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "response_template": self.response_template,
            "query": self.query,
        }


@dataclass(frozen=True, slots=True)
class ScriptedModelResult:
    """Bounded text returned by the scripted test provider."""

    text: str

    def __post_init__(self) -> None:
        _validate_result_text(self.text)

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text}


@dataclass(frozen=True, slots=True)
class ScriptedModelProvider:
    """Stateless offline renderer; deliberately not a production provider port."""

    def render(
        self,
        request: object = _MISSING_REQUEST,
        /,
        *extra_requests: object,
        **options: object,
    ) -> ScriptedModelResult:
        if (
            extra_requests
            or options
            or type(request) is not ScriptedModelRequest
        ):
            raise _provider_error("invalid request")

        # Rebuild the value so even an object mutated through low-level Python
        # escape hatches cannot bypass the public boundary validation.
        try:
            normalized = ScriptedModelRequest(
                response_template=request.response_template,
                query=request.query,
            )
        except AttributeError:
            raise _provider_error("invalid request") from None
        stripped_query = normalized.query.strip()
        _preflight_template(normalized.response_template, stripped_query)
        try:
            rendered = _render_once(normalized.response_template, stripped_query)
        except Exception:
            raise _provider_error("render failed") from None
        return ScriptedModelResult(rendered)


__all__ = [
    "ScriptedModelProvider",
    "ScriptedModelRequest",
    "ScriptedModelResult",
]
