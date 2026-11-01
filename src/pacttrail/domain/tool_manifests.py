from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
from typing import cast

from .definitions import (
    DefinitionRef,
    canonical_json as serialize_canonical_json,
    canonical_sha256,
    validate_definition_id,
    validate_semantic_version,
    validate_sha256,
)
from .runs import PactTrailError


_TOOL_MANIFEST_SCHEMA_VERSION = "pacttrail.tool.v1"
TOOL_MANIFEST_SCHEMA_VERSION = _TOOL_MANIFEST_SCHEMA_VERSION

_TOOL_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "tool_id",
        "version",
        "description",
        "input_schema",
        "output_schema",
        "implementation_kind",
        "risk_level",
        "side_effect",
        "allowed_scopes",
        "supports_dry_run",
        "supports_idempotency",
        "timeout_seconds",
        "max_output_bytes",
        "secret_fields",
        "retention_policy_ref",
    }
)
_DEFINITION_REF_FIELDS = frozenset({"id", "version", "digest"})
_IMPLEMENTATION_KINDS = frozenset({"pacttrail-adapter"})
_RISK_LEVELS = frozenset({"L0", "L1", "L2", "L3"})
_RISK_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
_SIDE_EFFECTS = frozenset(
    {"none", "recoverable", "external", "irreversible"}
)
_MINIMUM_RISK_BY_SIDE_EFFECT = {
    "none": 0,
    "recoverable": 1,
    "external": 2,
    "irreversible": 3,
}
_JSON_POINTER = re.compile(r"(?:/(?:[^~/]|~[01])*)+")

_VERSION_MAX_LENGTH = 128
_DESCRIPTION_MAX_LENGTH = 4096
_SET_LIKE_MAX_ITEMS = 64
_JSON_POINTER_MAX_LENGTH = 256
_SCHEMA_MAX_DEPTH = 64
_SCHEMA_MAX_CONTAINER_ITEMS = 256
_SCHEMA_MAX_NODES = 4096
_SCHEMA_KEY_MAX_LENGTH = 256
_SCHEMA_STRING_MAX_LENGTH = 16384
_SCHEMA_MAX_BYTES = 65536
_JSON_SAFE_INTEGER = 9007199254740991
_SCHEMA_MAX_FLOAT_ABS = 1e308
_TIMEOUT_MAX_SECONDS = 3600.0
_MAX_OUTPUT_BYTES = 1048576


def _invalid_tool_manifest(reason: str) -> PactTrailError:
    return PactTrailError(f"invalid_tool_manifest: {reason}")


def _validate_plain_utf8(
    value: object,
    *,
    field_name: str,
    max_length: int,
    require_nonempty: bool,
) -> str:
    if type(value) is not str:
        raise _invalid_tool_manifest(f"{field_name} must be text")
    if len(value) > max_length:
        raise _invalid_tool_manifest(
            f"{field_name} must contain at most {max_length} characters"
        )
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _invalid_tool_manifest(
            f"{field_name} must be valid UTF-8 text"
        ) from None
    if require_nonempty and not value.strip():
        raise _invalid_tool_manifest(f"{field_name} must not be empty")
    return value


def _validate_tool_id(value: object) -> str:
    if type(value) is not str:
        raise _invalid_tool_manifest(
            "tool_id must be a valid lower-case definition id"
        )
    try:
        validate_definition_id(value)
    except PactTrailError:
        raise _invalid_tool_manifest(
            "tool_id must be a valid lower-case definition id"
        ) from None
    return value


def _validate_version(value: object) -> str:
    if (
        type(value) is not str
        or len(value) > _VERSION_MAX_LENGTH
        or not value.isascii()
    ):
        raise _invalid_tool_manifest(
            "version must be an exact bounded semantic version"
        )
    try:
        validate_semantic_version(value)
    except PactTrailError:
        raise _invalid_tool_manifest(
            "version must be an exact bounded semantic version"
        ) from None
    return value


def _validate_schema_text(
    value: str,
    *,
    is_key: bool,
) -> int:
    maximum = _SCHEMA_KEY_MAX_LENGTH if is_key else _SCHEMA_STRING_MAX_LENGTH
    if len(value) > maximum:
        raise _invalid_tool_manifest(
            "schema must contain bounded canonical JSON values"
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise _invalid_tool_manifest(
            "schema must contain bounded canonical JSON values"
        ) from None
    return len(encoded)


def _validate_schema_value(
    value: object,
    *,
    depth: int = 0,
    active_containers: set[int] | None = None,
    node_count: list[int] | None = None,
    utf8_count: list[int] | None = None,
) -> None:
    if depth > _SCHEMA_MAX_DEPTH:
        raise _invalid_tool_manifest(
            "schema must contain bounded canonical JSON values"
        )

    count = node_count if node_count is not None else [0]
    count[0] += 1
    if count[0] > _SCHEMA_MAX_NODES:
        raise _invalid_tool_manifest(
            "schema must contain bounded canonical JSON values"
        )

    encoded_count = utf8_count if utf8_count is not None else [0]

    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -_JSON_SAFE_INTEGER <= value <= _JSON_SAFE_INTEGER:
            raise _invalid_tool_manifest(
                "schema must contain bounded canonical JSON values"
            )
        return
    if type(value) is float:
        if not math.isfinite(value) or abs(value) > _SCHEMA_MAX_FLOAT_ABS:
            raise _invalid_tool_manifest(
                "schema must contain bounded canonical JSON values"
            )
        return
    if type(value) is str:
        encoded_count[0] += _validate_schema_text(value, is_key=False)
        if encoded_count[0] > _SCHEMA_MAX_BYTES:
            raise _invalid_tool_manifest(
                "schema must contain bounded canonical JSON values"
            )
        return
    if type(value) not in (dict, list):
        raise _invalid_tool_manifest(
            "schema must contain bounded canonical JSON values"
        )
    if len(value) > _SCHEMA_MAX_CONTAINER_ITEMS:
        raise _invalid_tool_manifest(
            "schema must contain bounded canonical JSON values"
        )

    active = active_containers if active_containers is not None else set()
    container_id = id(value)
    if container_id in active:
        raise _invalid_tool_manifest(
            "schema must contain bounded canonical JSON values"
        )
    active.add(container_id)
    try:
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise _invalid_tool_manifest(
                        "schema must contain bounded canonical JSON values"
                    )
                encoded_count[0] += _validate_schema_text(key, is_key=True)
                if encoded_count[0] > _SCHEMA_MAX_BYTES:
                    raise _invalid_tool_manifest(
                        "schema must contain bounded canonical JSON values"
                    )
                _validate_schema_value(
                    item,
                    depth=depth + 1,
                    active_containers=active,
                    node_count=count,
                    utf8_count=encoded_count,
                )
        else:
            for item in value:
                _validate_schema_value(
                    item,
                    depth=depth + 1,
                    active_containers=active,
                    node_count=count,
                    utf8_count=encoded_count,
                )
    finally:
        active.remove(container_id)


def _canonicalize_schema(value: object, field_name: str) -> str:
    if type(value) is not dict:
        raise _invalid_tool_manifest(f"{field_name} must be a JSON object")
    _validate_schema_value(value)
    try:
        serialized = serialize_canonical_json(value)
    except PactTrailError:
        raise _invalid_tool_manifest(
            "schema must contain bounded canonical JSON values"
        ) from None
    if len(serialized.encode("utf-8")) > _SCHEMA_MAX_BYTES:
        raise _invalid_tool_manifest(
            "schema must contain bounded canonical JSON values"
        )
    return serialized


def _validate_enum(
    value: object,
    *,
    field_name: str,
    supported: frozenset[str],
) -> str:
    if (
        type(value) is not str
        or len(value) > max(len(item) for item in supported)
        or value not in supported
    ):
        raise _invalid_tool_manifest(
            f"{field_name} contains an unsupported value"
        )
    return value


def _normalize_stable_ids(value: object) -> tuple[str, ...]:
    if type(value) not in (list, tuple):
        raise _invalid_tool_manifest(
            "allowed_scopes must be a list of stable ids"
        )
    if len(value) > _SET_LIKE_MAX_ITEMS:
        raise _invalid_tool_manifest(
            "allowed_scopes must contain at most 64 items"
        )

    normalized: list[str] = []
    for item in value:
        if type(item) is not str:
            raise _invalid_tool_manifest(
                "allowed_scopes must contain plain stable ids"
            )
        try:
            validate_definition_id(item)
        except PactTrailError:
            raise _invalid_tool_manifest(
                "allowed_scopes must contain valid lower-case stable ids"
            ) from None
        normalized.append(item)
    if len(set(normalized)) != len(normalized):
        raise _invalid_tool_manifest(
            "allowed_scopes must not contain duplicates"
        )
    return tuple(sorted(normalized))


def _normalize_secret_fields(value: object) -> tuple[str, ...]:
    if type(value) not in (list, tuple):
        raise _invalid_tool_manifest(
            "secret_fields must be a list of JSON pointers"
        )
    if len(value) > _SET_LIKE_MAX_ITEMS:
        raise _invalid_tool_manifest(
            "secret_fields must contain at most 64 items"
        )

    normalized: list[str] = []
    for item in value:
        if type(item) is not str:
            raise _invalid_tool_manifest(
                "secret_fields must contain plain JSON pointers"
            )
        if (
            not item
            or len(item) > _JSON_POINTER_MAX_LENGTH
        ):
            raise _invalid_tool_manifest(
                "secret_fields must contain valid JSON pointers"
            )
        try:
            item.encode("utf-8")
        except UnicodeError:
            raise _invalid_tool_manifest(
                "secret_fields must contain valid JSON pointers"
            ) from None
        if not _JSON_POINTER.fullmatch(item):
            raise _invalid_tool_manifest(
                "secret_fields must contain valid JSON pointers"
            )
        normalized.append(item)
    if len(set(normalized)) != len(normalized):
        raise _invalid_tool_manifest(
            "secret_fields must not contain duplicates"
        )
    return tuple(sorted(normalized))


def _validate_boolean(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise _invalid_tool_manifest(f"{field_name} must be a boolean")
    return value


def _validate_timeout(value: object) -> float:
    if type(value) not in (int, float):
        raise _invalid_tool_manifest(
            "timeout_seconds must be a finite number"
        )
    try:
        normalized = float(value)
    except OverflowError:
        raise _invalid_tool_manifest(
            "timeout_seconds must be greater than 0 and at most 3600"
        ) from None
    if (
        not math.isfinite(normalized)
        or normalized <= 0
        or normalized > _TIMEOUT_MAX_SECONDS
    ):
        raise _invalid_tool_manifest(
            "timeout_seconds must be greater than 0 and at most 3600"
        )
    return normalized


def _validate_max_output_bytes(value: object) -> int:
    if type(value) is not int:
        raise _invalid_tool_manifest("max_output_bytes must be an integer")
    if not 1 <= value <= _MAX_OUTPUT_BYTES:
        raise _invalid_tool_manifest(
            "max_output_bytes must be between 1 and 1048576"
        )
    return value


def _validate_definition_ref(
    value: object,
    field_name: str,
) -> DefinitionRef | None:
    if value is None:
        return None
    if type(value) is not DefinitionRef:
        raise _invalid_tool_manifest(
            f"{field_name} must be an exact DefinitionRef or null"
        )
    try:
        parts = (value.definition_id, value.version, value.digest)
    except AttributeError:
        raise _invalid_tool_manifest(
            f"{field_name} must contain a valid exact definition ref"
        ) from None
    if any(type(part) is not str for part in parts):
        raise _invalid_tool_manifest(
            f"{field_name} must contain plain JSON strings"
        )
    try:
        validate_definition_id(parts[0])
        if len(parts[1]) > _VERSION_MAX_LENGTH or not parts[1].isascii():
            raise PactTrailError("bounded ref version required")
        validate_semantic_version(parts[1])
        validate_sha256(parts[2])
    except PactTrailError:
        raise _invalid_tool_manifest(
            f"{field_name} must contain a valid exact definition ref"
        ) from None
    return DefinitionRef(parts[0], parts[1], parts[2])


def _parse_definition_ref(
    value: object,
    field_name: str,
) -> DefinitionRef | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise _invalid_tool_manifest(
            f"{field_name} must be null or an exact definition ref"
        )
    if len(value) != len(_DEFINITION_REF_FIELDS):
        raise _invalid_tool_manifest(
            f"{field_name} must contain exactly id, version, and digest"
        )
    if any(type(key) is not str for key in value):
        raise _invalid_tool_manifest(
            f"{field_name} must contain exactly id, version, and digest"
        )
    if set(value) != _DEFINITION_REF_FIELDS:
        raise _invalid_tool_manifest(
            f"{field_name} must contain exactly id, version, and digest"
        )
    if any(type(value[key]) is not str for key in _DEFINITION_REF_FIELDS):
        raise _invalid_tool_manifest(
            f"{field_name} must contain plain JSON strings"
        )
    try:
        if (
            len(value["version"]) > _VERSION_MAX_LENGTH
            or not value["version"].isascii()
        ):
            raise PactTrailError("bounded ref version required")
        return DefinitionRef(
            definition_id=value["id"],
            version=value["version"],
            digest=value["digest"],
        )
    except PactTrailError:
        raise _invalid_tool_manifest(
            f"{field_name} must contain a valid exact definition ref"
        ) from None


@dataclass(frozen=True, slots=True, init=False)
class ToolManifest:
    """Immutable, canonical PactTrail Tool declaration schema v1."""

    tool_id: str
    version: str
    description: str
    _input_schema_json: str = field(repr=False)
    _output_schema_json: str = field(repr=False)
    implementation_kind: str
    risk_level: str
    side_effect: str
    allowed_scopes: tuple[str, ...]
    supports_dry_run: bool
    supports_idempotency: bool
    timeout_seconds: float
    max_output_bytes: int
    secret_fields: tuple[str, ...]
    retention_policy_ref: DefinitionRef | None

    def __init__(
        self,
        tool_id: str,
        version: str,
        description: str,
        input_schema: dict[str, object],
        output_schema: dict[str, object],
        implementation_kind: str,
        risk_level: str,
        side_effect: str,
        allowed_scopes: list[str] | tuple[str, ...],
        supports_dry_run: bool,
        supports_idempotency: bool,
        timeout_seconds: int | float,
        max_output_bytes: int,
        secret_fields: list[str] | tuple[str, ...],
        retention_policy_ref: DefinitionRef | None,
    ) -> None:
        normalized_tool_id = _validate_tool_id(tool_id)
        normalized_version = _validate_version(version)
        normalized_description = _validate_plain_utf8(
            description,
            field_name="description",
            max_length=_DESCRIPTION_MAX_LENGTH,
            require_nonempty=True,
        )
        input_schema_json = _canonicalize_schema(
            input_schema,
            "input_schema",
        )
        output_schema_json = _canonicalize_schema(
            output_schema,
            "output_schema",
        )
        normalized_implementation_kind = _validate_enum(
            implementation_kind,
            field_name="implementation_kind",
            supported=_IMPLEMENTATION_KINDS,
        )
        normalized_risk_level = _validate_enum(
            risk_level,
            field_name="risk_level",
            supported=_RISK_LEVELS,
        )
        normalized_side_effect = _validate_enum(
            side_effect,
            field_name="side_effect",
            supported=_SIDE_EFFECTS,
        )
        normalized_scopes = _normalize_stable_ids(allowed_scopes)
        normalized_dry_run = _validate_boolean(
            supports_dry_run,
            "supports_dry_run",
        )
        normalized_idempotency = _validate_boolean(
            supports_idempotency,
            "supports_idempotency",
        )
        normalized_timeout = _validate_timeout(timeout_seconds)
        normalized_max_output = _validate_max_output_bytes(max_output_bytes)
        normalized_secret_fields = _normalize_secret_fields(secret_fields)
        normalized_retention_ref = _validate_definition_ref(
            retention_policy_ref,
            "retention_policy_ref",
        )

        if (
            _RISK_RANK[normalized_risk_level]
            < _MINIMUM_RISK_BY_SIDE_EFFECT[normalized_side_effect]
        ):
            raise _invalid_tool_manifest(
                "risk_level is below the side_effect minimum"
            )
        if normalized_side_effect == "recoverable" and not normalized_dry_run:
            raise _invalid_tool_manifest(
                "recoverable side_effect requires dry-run support"
            )
        if normalized_side_effect != "none" and not normalized_scopes:
            raise _invalid_tool_manifest(
                "side-effecting tools require at least one allowed scope"
            )

        object.__setattr__(self, "tool_id", normalized_tool_id)
        object.__setattr__(self, "version", normalized_version)
        object.__setattr__(self, "description", normalized_description)
        object.__setattr__(self, "_input_schema_json", input_schema_json)
        object.__setattr__(self, "_output_schema_json", output_schema_json)
        object.__setattr__(
            self,
            "implementation_kind",
            normalized_implementation_kind,
        )
        object.__setattr__(self, "risk_level", normalized_risk_level)
        object.__setattr__(self, "side_effect", normalized_side_effect)
        object.__setattr__(self, "allowed_scopes", normalized_scopes)
        object.__setattr__(self, "supports_dry_run", normalized_dry_run)
        object.__setattr__(
            self,
            "supports_idempotency",
            normalized_idempotency,
        )
        object.__setattr__(self, "timeout_seconds", normalized_timeout)
        object.__setattr__(self, "max_output_bytes", normalized_max_output)
        object.__setattr__(self, "secret_fields", normalized_secret_fields)
        object.__setattr__(
            self,
            "retention_policy_ref",
            normalized_retention_ref,
        )

    @property
    def schema_version(self) -> str:
        return _TOOL_MANIFEST_SCHEMA_VERSION

    @property
    def input_schema(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self._input_schema_json))

    @property
    def output_schema(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self._output_schema_json))

    @classmethod
    def from_dict(cls, value: object) -> ToolManifest:
        if type(value) is not dict:
            raise _invalid_tool_manifest("payload must be a JSON object")
        if len(value) > len(_TOOL_MANIFEST_FIELDS):
            raise _invalid_tool_manifest("payload contains unknown fields")
        if any(type(key) is not str for key in value):
            raise _invalid_tool_manifest(
                "payload keys must be plain JSON strings"
            )
        keys = set(value)
        if keys - _TOOL_MANIFEST_FIELDS:
            raise _invalid_tool_manifest("payload contains unknown fields")
        missing = _TOOL_MANIFEST_FIELDS - keys
        if missing:
            raise _invalid_tool_manifest(
                "payload is missing required fields"
            )
        if (
            type(value["schema_version"]) is not str
            or value["schema_version"] != _TOOL_MANIFEST_SCHEMA_VERSION
        ):
            raise _invalid_tool_manifest(
                "schema_version must be pacttrail.tool.v1"
            )
        if type(value["allowed_scopes"]) is not list:
            raise _invalid_tool_manifest("allowed_scopes must be a JSON array")
        if type(value["secret_fields"]) is not list:
            raise _invalid_tool_manifest("secret_fields must be a JSON array")

        return cls(
            tool_id=value["tool_id"],
            version=value["version"],
            description=value["description"],
            input_schema=value["input_schema"],
            output_schema=value["output_schema"],
            implementation_kind=value["implementation_kind"],
            risk_level=value["risk_level"],
            side_effect=value["side_effect"],
            allowed_scopes=value["allowed_scopes"],
            supports_dry_run=value["supports_dry_run"],
            supports_idempotency=value["supports_idempotency"],
            timeout_seconds=value["timeout_seconds"],
            max_output_bytes=value["max_output_bytes"],
            secret_fields=value["secret_fields"],
            retention_policy_ref=_parse_definition_ref(
                value["retention_policy_ref"],
                "retention_policy_ref",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _TOOL_MANIFEST_SCHEMA_VERSION,
            "tool_id": self.tool_id,
            "version": self.version,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "implementation_kind": self.implementation_kind,
            "risk_level": self.risk_level,
            "side_effect": self.side_effect,
            "allowed_scopes": list(self.allowed_scopes),
            "supports_dry_run": self.supports_dry_run,
            "supports_idempotency": self.supports_idempotency,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "secret_fields": list(self.secret_fields),
            "retention_policy_ref": (
                self.retention_policy_ref.to_dict()
                if self.retention_policy_ref is not None
                else None
            ),
        }

    def canonical_payload(self) -> dict[str, object]:
        return self.to_dict()

    def canonical_json(self) -> str:
        return serialize_canonical_json(self.canonical_payload())

    @property
    def digest(self) -> str:
        return canonical_sha256(self.canonical_payload())

    @property
    def definition_ref(self) -> DefinitionRef:
        return DefinitionRef(self.tool_id, self.version, self.digest)
