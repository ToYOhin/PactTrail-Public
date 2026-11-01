from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from string import Formatter
from typing import Any

from .runs import PactTrailError


_DEFINITION_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_SEMANTIC_VERSION = re.compile(
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_QUERY_TEMPLATE_FIELD = re.compile(
    r"query(?:(?:\.[A-Za-z_][A-Za-z0-9_]*)|(?:\[[0-9]+\]))*"
)

_AGENT_DEFINITION_SCHEMA_VERSION = "pacttrail.agent.v1"
AGENT_DEFINITION_SCHEMA_VERSION = _AGENT_DEFINITION_SCHEMA_VERSION
_LEGACY_SCRIPTED_AGENT_SCHEMA_VERSION = "pacttrail.scripted-agent.v1"
_DETERMINISTIC_TEMPLATE_KIND = "deterministic-template"
_AGENT_DEFINITION_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "agent_id",
        "version",
        "role",
        "objective",
        "execution",
    }
)
_AGENT_DEFINITION_OPTIONAL_FIELDS = frozenset(
    {
        "input_schema_ref",
        "output_schema_ref",
        "prompt_ref",
    }
)
_EXECUTION_FIELDS = frozenset(
    {"kind", "response_template", "strip_query"}
)
_DEFINITION_REF_FIELDS = frozenset({"id", "version", "digest"})
_ROLE_MAX_LENGTH = 128
_OBJECTIVE_MAX_LENGTH = 4096
_RESPONSE_TEMPLATE_MAX_LENGTH = 16384
_JSON_MAX_DEPTH = 64


def validate_definition_id(value: str) -> str:
    if not isinstance(value, str) or not _DEFINITION_ID.fullmatch(value):
        raise PactTrailError(
            "definition id must contain 1-128 lower-case letters, digits, dots, "
            "underscores, or hyphens"
        )
    return value


def validate_semantic_version(value: str) -> str:
    if not isinstance(value, str) or not _SEMANTIC_VERSION.fullmatch(value):
        raise PactTrailError("definition version must be an exact semantic version")
    return value


def validate_sha256(value: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PactTrailError("definition digest must be a lower-case SHA-256 value")
    return value


def canonical_json(value: Any) -> str:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        serialized.encode("utf-8")
        return serialized
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PactTrailError(
            f"definition is not canonical JSON compatible: {type(exc).__name__}"
        ) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DefinitionRef:
    definition_id: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        validate_definition_id(self.definition_id)
        validate_semantic_version(self.version)
        validate_sha256(self.digest)

    @classmethod
    def from_payload(
        cls,
        definition_id: str,
        version: str,
        payload: Any,
    ) -> DefinitionRef:
        return cls(
            definition_id=definition_id,
            version=version,
            digest=canonical_sha256(payload),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.definition_id,
            "version": self.version,
            "digest": self.digest,
        }


def _invalid_agent_definition(reason: str) -> PactTrailError:
    return PactTrailError(f"invalid_agent_definition: {reason}")


def _validate_json_value(
    value: object,
    *,
    depth: int = 0,
    active_containers: set[int] | None = None,
) -> None:
    if depth > _JSON_MAX_DEPTH:
        raise _invalid_agent_definition(
            "payload must contain canonical JSON values"
        )

    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise _invalid_agent_definition(
                "payload must contain canonical JSON values"
            )
        return
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise _invalid_agent_definition(
                "payload must contain canonical JSON values"
            ) from None
        return
    if type(value) not in (dict, list):
        raise _invalid_agent_definition(
            "payload must contain canonical JSON values"
        )

    active = active_containers if active_containers is not None else set()
    container_id = id(value)
    if container_id in active:
        raise _invalid_agent_definition(
            "payload must contain canonical JSON values"
        )
    active.add(container_id)
    try:
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise _invalid_agent_definition(
                        "payload must contain canonical JSON values"
                    )
                _validate_json_value(
                    key,
                    depth=depth + 1,
                    active_containers=active,
                )
                _validate_json_value(
                    item,
                    depth=depth + 1,
                    active_containers=active,
                )
        else:
            for item in value:
                _validate_json_value(
                    item,
                    depth=depth + 1,
                    active_containers=active,
                )
    finally:
        active.remove(container_id)


def _validate_agent_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if type(value) is not str:
        raise _invalid_agent_definition(f"{field_name} must be text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _invalid_agent_definition(
            f"{field_name} must be valid UTF-8 text"
        ) from None
    if not value.strip():
        raise _invalid_agent_definition(f"{field_name} must not be empty")
    if len(value) > max_length:
        raise _invalid_agent_definition(
            f"{field_name} must contain at most {max_length} characters"
        )
    return value


def _validate_response_template(value: object, *, depth: int = 0) -> str:
    if depth > _JSON_MAX_DEPTH:
        raise _invalid_agent_definition(
            "response_template nesting must not exceed 64 levels"
        )
    if type(value) is not str:
        raise _invalid_agent_definition("response_template must be text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _invalid_agent_definition(
            "response_template must be valid UTF-8 text"
        ) from None
    if len(value) > _RESPONSE_TEMPLATE_MAX_LENGTH:
        raise _invalid_agent_definition(
            "response_template must contain at most "
            f"{_RESPONSE_TEMPLATE_MAX_LENGTH} characters"
        )
    try:
        for _, field_name, format_spec, conversion in Formatter().parse(value):
            if field_name is None:
                continue
            if not _QUERY_TEMPLATE_FIELD.fullmatch(field_name):
                raise _invalid_agent_definition(
                    "response_template fields must use a valid query path"
                )
            if conversion not in (None, "s", "r", "a"):
                raise _invalid_agent_definition(
                    "response_template contains an unsupported conversion"
                )
            if "{" in format_spec or "}" in format_spec:
                if depth >= 1:
                    raise _invalid_agent_definition(
                        "response_template nested fields may be one level deep"
                    )
                _validate_response_template(format_spec, depth=depth + 1)
            else:
                try:
                    format("", format_spec)
                except (TypeError, ValueError):
                    raise _invalid_agent_definition(
                        "response_template contains an invalid string format spec"
                    ) from None
    except ValueError:
        raise _invalid_agent_definition(
            "response_template must be a valid deterministic template"
        ) from None
    return value


@dataclass(frozen=True, slots=True)
class DeterministicTemplateExecution:
    """Closed v1 execution configuration for the offline scripted Agent."""

    response_template: str
    strip_query: bool = True

    @property
    def kind(self) -> str:
        return _DETERMINISTIC_TEMPLATE_KIND

    def __post_init__(self) -> None:
        _validate_response_template(self.response_template)
        if type(self.strip_query) is not bool:
            raise _invalid_agent_definition("strip_query must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": _DETERMINISTIC_TEMPLATE_KIND,
            "response_template": self.response_template,
            "strip_query": self.strip_query,
        }


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Immutable, canonical PactTrail Agent definition schema v1."""

    agent_id: str
    version: str
    role: str
    objective: str
    execution: DeterministicTemplateExecution
    input_schema_ref: DefinitionRef | None = None
    output_schema_ref: DefinitionRef | None = None
    prompt_ref: DefinitionRef | None = None

    @property
    def schema_version(self) -> str:
        return _AGENT_DEFINITION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.agent_id) is not str:
            raise _invalid_agent_definition(
                "agent_id must be a valid lower-case definition id"
            )
        try:
            validate_definition_id(self.agent_id)
        except PactTrailError:
            raise _invalid_agent_definition(
                "agent_id must be a valid lower-case definition id"
            ) from None
        if type(self.version) is not str:
            raise _invalid_agent_definition(
                "version must be an exact semantic version"
            )
        try:
            validate_semantic_version(self.version)
        except PactTrailError:
            raise _invalid_agent_definition(
                "version must be an exact semantic version"
            ) from None
        _validate_agent_text(
            self.role,
            field_name="role",
            max_length=_ROLE_MAX_LENGTH,
        )
        _validate_agent_text(
            self.objective,
            field_name="objective",
            max_length=_OBJECTIVE_MAX_LENGTH,
        )
        if type(self.execution) is not DeterministicTemplateExecution:
            raise _invalid_agent_definition(
                "execution must be deterministic-template configuration"
            )
        for field_name in (
            "input_schema_ref",
            "output_schema_ref",
            "prompt_ref",
        ):
            reference = getattr(self, field_name)
            if reference is not None and type(reference) is not DefinitionRef:
                raise _invalid_agent_definition(
                    f"{field_name} must be an exact DefinitionRef or null"
                )
            if reference is not None and any(
                type(part) is not str
                for part in (
                    reference.definition_id,
                    reference.version,
                    reference.digest,
                )
            ):
                raise _invalid_agent_definition(
                    f"{field_name} must contain plain JSON strings"
                )

    @classmethod
    def from_dict(cls, value: object) -> AgentDefinition:
        if type(value) is not dict:
            raise _invalid_agent_definition("payload must be a JSON object")
        _validate_json_value(value)

        keys = set(value)
        missing = _AGENT_DEFINITION_REQUIRED_FIELDS - keys
        if missing:
            raise _invalid_agent_definition(
                f"missing fields: {', '.join(sorted(missing))}"
            )
        unknown = keys - (
            _AGENT_DEFINITION_REQUIRED_FIELDS
            | _AGENT_DEFINITION_OPTIONAL_FIELDS
        )
        if unknown:
            raise _invalid_agent_definition(
                f"unknown fields: {', '.join(sorted(unknown))}"
            )
        if value["schema_version"] != _AGENT_DEFINITION_SCHEMA_VERSION:
            raise _invalid_agent_definition(
                "schema_version must be pacttrail.agent.v1"
            )

        execution_value = value["execution"]
        if type(execution_value) is not dict:
            raise _invalid_agent_definition("execution must be a JSON object")
        execution_keys = set(execution_value)
        missing_execution = _EXECUTION_FIELDS - execution_keys
        if missing_execution:
            raise _invalid_agent_definition(
                "execution is missing fields: "
                f"{', '.join(sorted(missing_execution))}"
            )
        unknown_execution = execution_keys - _EXECUTION_FIELDS
        if unknown_execution:
            raise _invalid_agent_definition(
                "execution has unknown fields: "
                f"{', '.join(sorted(unknown_execution))}"
            )
        if execution_value["kind"] != _DETERMINISTIC_TEMPLATE_KIND:
            raise _invalid_agent_definition(
                "execution.kind must be deterministic-template"
            )

        execution = DeterministicTemplateExecution(
            response_template=execution_value["response_template"],
            strip_query=execution_value["strip_query"],
        )
        return cls(
            agent_id=value["agent_id"],
            version=value["version"],
            role=value["role"],
            objective=value["objective"],
            execution=execution,
            input_schema_ref=_parse_agent_definition_ref(
                value.get("input_schema_ref"),
                "input_schema_ref",
            ),
            output_schema_ref=_parse_agent_definition_ref(
                value.get("output_schema_ref"),
                "output_schema_ref",
            ),
            prompt_ref=_parse_agent_definition_ref(
                value.get("prompt_ref"),
                "prompt_ref",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _AGENT_DEFINITION_SCHEMA_VERSION,
            "agent_id": self.agent_id,
            "version": self.version,
            "role": self.role,
            "objective": self.objective,
            "input_schema_ref": (
                self.input_schema_ref.to_dict()
                if self.input_schema_ref is not None
                else None
            ),
            "output_schema_ref": (
                self.output_schema_ref.to_dict()
                if self.output_schema_ref is not None
                else None
            ),
            "prompt_ref": (
                self.prompt_ref.to_dict()
                if self.prompt_ref is not None
                else None
            ),
            "execution": self.execution.to_dict(),
        }

    def canonical_payload(self) -> dict[str, object]:
        return self.to_dict()

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_payload())

    @property
    def digest(self) -> str:
        return canonical_sha256(self.canonical_payload())

    @property
    def definition_ref(self) -> DefinitionRef:
        return DefinitionRef(self.agent_id, self.version, self.digest)


def _parse_agent_definition_ref(
    value: object,
    field_name: str,
) -> DefinitionRef | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise _invalid_agent_definition(
            f"{field_name} must be null or an exact definition ref"
        )
    keys = set(value)
    if keys != _DEFINITION_REF_FIELDS:
        raise _invalid_agent_definition(
            f"{field_name} must contain exactly id, version, and digest"
        )
    try:
        return DefinitionRef(
            definition_id=value["id"],
            version=value["version"],
            digest=value["digest"],
        )
    except PactTrailError:
        raise _invalid_agent_definition(
            f"{field_name} must contain a valid exact definition ref"
        ) from None


def legacy_scripted_definition_ref(
    definition: AgentDefinition,
) -> DefinitionRef:
    """Project a v1 scripted definition to the pre-M2-C persisted identity."""

    if type(definition) is not AgentDefinition:
        raise _invalid_agent_definition(
            "legacy scripted identity requires an AgentDefinition"
        )
    payload = {
        "schema_version": _LEGACY_SCRIPTED_AGENT_SCHEMA_VERSION,
        "agent_id": definition.agent_id,
        "version": definition.version,
        "kind": _DETERMINISTIC_TEMPLATE_KIND,
        "response_template": definition.execution.response_template,
        "strip_query": definition.execution.strip_query,
    }
    return DefinitionRef.from_payload(
        definition.agent_id,
        definition.version,
        payload,
    )
