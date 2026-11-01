from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import re
from typing import cast

from .definitions import (
    canonical_json as serialize_canonical_json,
    canonical_sha256,
    validate_definition_id,
    validate_semantic_version,
    validate_sha256,
)
from .runs import PactTrailError, validate_run_id


_EVENT_ENVELOPE_SCHEMA_VERSION = "pacttrail.event-envelope.v1"
EVENT_ENVELOPE_SCHEMA_VERSION = _EVENT_ENVELOPE_SCHEMA_VERSION

_EVENT_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "event_type",
        "run_id",
        "sequence",
        "occurred_at",
        "recorded_at",
        "actor",
        "entity_ref",
        "correlation_id",
        "causation_id",
        "definition_refs",
        "payload_schema_version",
        "payload",
        "redaction",
    }
)
_EVENT_IDENTITY_FIELDS = frozenset({"event_id", "digest"})
_ACTOR_FIELDS = frozenset({"kind", "id"})
_ENTITY_FIELDS = frozenset({"kind", "id"})
_DEFINITION_REF_FIELDS = frozenset({"role", "id", "version", "digest"})
_REDACTION_FIELDS = frozenset({"status", "paths"})

_EVENT_ENTITY_KIND_BY_TYPE = {
    "run.created": "run",
    "run.started": "run",
    "run.cancel_requested": "run",
    "run.succeeded": "run",
    "run.failed": "run",
    "run.canceled": "run",
    "step.queued": "step",
    "step.started": "step",
    "step.waiting_approval": "step",
    "step.retry_scheduled": "step",
    "step.succeeded": "step",
    "step.failed": "step",
    "step.canceled": "step",
    "agent.started": "agent_invocation",
    "agent.completed": "agent_invocation",
    "agent.failed": "agent_invocation",
    "provider.requested": "provider_request",
    "provider.completed": "provider_request",
    "provider.failed": "provider_request",
    "tool.proposed": "tool_invocation",
    "tool.policy_decided": "tool_invocation",
    "tool.started": "tool_invocation",
    "tool.completed": "tool_invocation",
    "tool.failed": "tool_invocation",
    "tool.blocked": "tool_invocation",
    "approval.requested": "approval",
    "approval.decided": "approval",
    "approval.consumed": "approval",
    "approval.expired": "approval",
    "artifact.staged": "artifact",
    "artifact.available": "artifact",
    "artifact.quarantined": "artifact",
    "evaluation.started": "evaluation",
    "evaluation.case_completed": "evaluation",
    "evaluation.completed": "evaluation",
}
_EVENT_TYPES = frozenset(_EVENT_ENTITY_KIND_BY_TYPE)
_ACTOR_KINDS = frozenset({"system", "operator", "agent", "worker"})
_ENTITY_KINDS = frozenset(
    {
        "run",
        "step",
        "agent_invocation",
        "provider_request",
        "tool_invocation",
        "approval",
        "artifact",
        "evaluation",
    }
)
_DEFINITION_ROLES = frozenset({"flow", "agent", "tool", "policy"})
_REDACTION_STATUSES = frozenset({"not_applied", "applied"})

_EVENT_LOCAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_CANONICAL_UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00"
)
_JSON_POINTER = re.compile(r"(?:/(?:[^~/]|~[01])*)*")

_ID_MAX_LENGTH = 128
_VERSION_MAX_LENGTH = 128
_TIMESTAMP_LENGTH = 32
_SEQUENCE_MAXIMUM = 9007199254740991
_DEFINITION_REFS_MAX_ITEMS = 64
_REDACTION_PATHS_MAX_ITEMS = 64
_JSON_POINTER_MAX_LENGTH = 256
_PAYLOAD_MAX_DEPTH = 64
_PAYLOAD_MAX_CONTAINER_ITEMS = 256
_PAYLOAD_MAX_NODES = 4096
_PAYLOAD_KEY_MAX_LENGTH = 256
_PAYLOAD_STRING_MAX_LENGTH = 16384
_PAYLOAD_MAX_BYTES = 262144
_ENVELOPE_MAX_BYTES = 327680
_JSON_SAFE_INTEGER = 9007199254740991
_PAYLOAD_MAX_FLOAT_ABS = 1e308


def _invalid_event_envelope(reason: str) -> PactTrailError:
    return PactTrailError(f"invalid_event_envelope: {reason}")


def _require_exact_object(
    value: object,
    *,
    fields: frozenset[str],
    field_name: str,
) -> dict[str, object]:
    if type(value) is not dict:
        raise _invalid_event_envelope(f"{field_name} must be a JSON object")
    if len(value) > len(fields):
        raise _invalid_event_envelope(f"{field_name} contains unknown fields")
    if any(type(key) is not str for key in value):
        raise _invalid_event_envelope(
            f"{field_name} keys must be plain JSON strings"
        )
    keys = set(value)
    if keys - fields:
        raise _invalid_event_envelope(f"{field_name} contains unknown fields")
    if fields - keys:
        raise _invalid_event_envelope(
            f"{field_name} is missing required fields"
        )
    return cast(dict[str, object], value)


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
        raise _invalid_event_envelope(
            f"{field_name} contains an unsupported value"
        )
    return value


def _validate_event_local_id(value: object, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) > _ID_MAX_LENGTH
        or not value.isascii()
        or not _EVENT_LOCAL_ID.fullmatch(value)
    ):
        raise _invalid_event_envelope(
            f"{field_name} must be a valid bounded event id"
        )
    return value


def _validate_nullable_event_local_id(
    value: object,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return _validate_event_local_id(value, field_name)


def _validate_run_id(value: object) -> str:
    if (
        type(value) is not str
        or len(value) > _ID_MAX_LENGTH
        or not value.isascii()
    ):
        raise _invalid_event_envelope("run_id must be a valid bounded run id")
    try:
        return validate_run_id(value)
    except PactTrailError:
        raise _invalid_event_envelope(
            "run_id must be a valid bounded run id"
        ) from None


def _validate_sequence(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _SEQUENCE_MAXIMUM:
        raise _invalid_event_envelope(
            "sequence must be an integer between 1 and 9007199254740991"
        )
    return value


def _validate_payload_schema_version(value: object) -> int:
    if type(value) is not int or value != 1:
        raise _invalid_event_envelope("payload_schema_version must be 1")
    return value


def _validate_timestamp(value: object, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != _TIMESTAMP_LENGTH
        or not value.isascii()
        or not _CANONICAL_UTC_TIMESTAMP.fullmatch(value)
    ):
        raise _invalid_event_envelope(
            f"{field_name} must be a canonical UTC timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, OverflowError):
        raise _invalid_event_envelope(
            f"{field_name} must be a canonical UTC timestamp"
        ) from None
    if parsed.isoformat(timespec="microseconds") != value:
        raise _invalid_event_envelope(
            f"{field_name} must be a canonical UTC timestamp"
        )
    return value


def _validate_definition_parts(
    definition_id: object,
    version: object,
    digest: object,
) -> tuple[str, str, str]:
    if any(type(part) is not str for part in (definition_id, version, digest)):
        raise _invalid_event_envelope(
            "definition ref must contain plain JSON strings"
        )
    try:
        validate_definition_id(definition_id)
        if len(version) > _VERSION_MAX_LENGTH or not version.isascii():
            raise PactTrailError("bounded version required")
        validate_semantic_version(version)
        validate_sha256(digest)
    except PactTrailError:
        raise _invalid_event_envelope(
            "definition ref must contain a valid exact identity"
        ) from None
    return definition_id, version, digest


def _validate_json_text(value: str, *, is_key: bool) -> int:
    maximum = _PAYLOAD_KEY_MAX_LENGTH if is_key else _PAYLOAD_STRING_MAX_LENGTH
    if len(value) > maximum:
        raise _invalid_event_envelope(
            "payload must contain bounded canonical JSON values"
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise _invalid_event_envelope(
            "payload must contain bounded canonical JSON values"
        ) from None
    return len(encoded)


def _validate_json_value(
    value: object,
    *,
    depth: int = 0,
    active_containers: set[int] | None = None,
    node_count: list[int] | None = None,
    utf8_count: list[int] | None = None,
) -> None:
    if depth > _PAYLOAD_MAX_DEPTH:
        raise _invalid_event_envelope(
            "payload must contain bounded canonical JSON values"
        )

    count = node_count if node_count is not None else [0]
    count[0] += 1
    if count[0] > _PAYLOAD_MAX_NODES:
        raise _invalid_event_envelope(
            "payload must contain bounded canonical JSON values"
        )

    encoded_count = utf8_count if utf8_count is not None else [0]

    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -_JSON_SAFE_INTEGER <= value <= _JSON_SAFE_INTEGER:
            raise _invalid_event_envelope(
                "payload must contain bounded canonical JSON values"
            )
        return
    if type(value) is float:
        if not math.isfinite(value) or abs(value) > _PAYLOAD_MAX_FLOAT_ABS:
            raise _invalid_event_envelope(
                "payload must contain bounded canonical JSON values"
            )
        return
    if type(value) is str:
        encoded_count[0] += _validate_json_text(value, is_key=False)
        if encoded_count[0] > _PAYLOAD_MAX_BYTES:
            raise _invalid_event_envelope(
                "payload must contain bounded canonical JSON values"
            )
        return
    if type(value) not in (dict, list):
        raise _invalid_event_envelope(
            "payload must contain bounded canonical JSON values"
        )
    if len(value) > _PAYLOAD_MAX_CONTAINER_ITEMS:
        raise _invalid_event_envelope(
            "payload must contain bounded canonical JSON values"
        )

    active = active_containers if active_containers is not None else set()
    container_id = id(value)
    if container_id in active:
        raise _invalid_event_envelope(
            "payload must contain bounded canonical JSON values"
        )
    active.add(container_id)
    try:
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise _invalid_event_envelope(
                        "payload must contain bounded canonical JSON values"
                    )
                encoded_count[0] += _validate_json_text(key, is_key=True)
                if encoded_count[0] > _PAYLOAD_MAX_BYTES:
                    raise _invalid_event_envelope(
                        "payload must contain bounded canonical JSON values"
                    )
                _validate_json_value(
                    item,
                    depth=depth + 1,
                    active_containers=active,
                    node_count=count,
                    utf8_count=encoded_count,
                )
        else:
            for item in value:
                _validate_json_value(
                    item,
                    depth=depth + 1,
                    active_containers=active,
                    node_count=count,
                    utf8_count=encoded_count,
                )
    finally:
        active.remove(container_id)


def _canonicalize_payload(value: object) -> str:
    if type(value) is not dict:
        raise _invalid_event_envelope("payload must be a JSON object")
    _validate_json_value(value)
    try:
        serialized = serialize_canonical_json(value)
    except PactTrailError:
        raise _invalid_event_envelope(
            "payload must contain bounded canonical JSON values"
        ) from None
    if len(serialized.encode("utf-8")) > _PAYLOAD_MAX_BYTES:
        raise _invalid_event_envelope(
            "payload must contain bounded canonical JSON values"
        )
    return serialized


def _normalize_definition_refs(
    value: object,
) -> tuple[EventDefinitionRef, ...]:
    if type(value) not in (list, tuple):
        raise _invalid_event_envelope(
            "definition_refs must be a list of exact event definition refs"
        )
    if len(value) > _DEFINITION_REFS_MAX_ITEMS:
        raise _invalid_event_envelope(
            "definition_refs must contain at most 64 items"
        )

    normalized: list[EventDefinitionRef] = []
    exact_identities: set[tuple[str, str, str, str]] = set()
    bound_identities: dict[tuple[str, str, str], str] = {}
    for item in value:
        if type(item) is not EventDefinitionRef:
            raise _invalid_event_envelope(
                "definition_refs must contain exact EventDefinitionRef values"
            )
        try:
            parts = (item.role, item.definition_id, item.version, item.digest)
        except AttributeError:
            raise _invalid_event_envelope(
                "definition_refs must contain valid exact refs"
            ) from None
        cloned = EventDefinitionRef(*parts)
        identity = (
            cloned.role,
            cloned.definition_id,
            cloned.version,
            cloned.digest,
        )
        binding = identity[:3]
        if identity in exact_identities:
            raise _invalid_event_envelope(
                "definition_refs must not contain duplicates"
            )
        previous_digest = bound_identities.get(binding)
        if previous_digest is not None and previous_digest != cloned.digest:
            raise _invalid_event_envelope(
                "definition_refs contain a conflicting identity"
            )
        exact_identities.add(identity)
        bound_identities[binding] = cloned.digest
        normalized.append(cloned)
    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                item.role,
                item.definition_id,
                item.version,
                item.digest,
            ),
        )
    )


def _parse_definition_refs(value: object) -> list[EventDefinitionRef]:
    if type(value) is not list:
        raise _invalid_event_envelope(
            "definition_refs must be a JSON array"
        )
    if len(value) > _DEFINITION_REFS_MAX_ITEMS:
        raise _invalid_event_envelope(
            "definition_refs must contain at most 64 items"
        )
    return [EventDefinitionRef.from_dict(item) for item in value]


@dataclass(frozen=True, slots=True, init=False)
class EventIdentity:
    """Stable content identity for one Event occurrence."""

    event_id: str
    digest: str

    def __init__(self, event_id: str, digest: str) -> None:
        normalized_event_id = _validate_event_local_id(event_id, "event_id")
        if type(digest) is not str:
            raise _invalid_event_envelope(
                "event identity digest must be a lower-case SHA-256"
            )
        try:
            validate_sha256(digest)
        except PactTrailError:
            raise _invalid_event_envelope(
                "event identity digest must be a lower-case SHA-256"
            ) from None
        object.__setattr__(self, "event_id", normalized_event_id)
        object.__setattr__(self, "digest", digest)

    @classmethod
    def from_dict(cls, value: object) -> EventIdentity:
        payload = _require_exact_object(
            value,
            fields=_EVENT_IDENTITY_FIELDS,
            field_name="event identity",
        )
        return cls(payload["event_id"], payload["digest"])

    def to_dict(self) -> dict[str, str]:
        return {"event_id": self.event_id, "digest": self.digest}


@dataclass(frozen=True, slots=True, init=False)
class EventActorRef:
    """Caller-declared actor reference; it is not an authentication claim."""

    kind: str
    actor_id: str

    def __init__(self, kind: str, actor_id: str) -> None:
        object.__setattr__(
            self,
            "kind",
            _validate_enum(kind, field_name="actor kind", supported=_ACTOR_KINDS),
        )
        object.__setattr__(
            self,
            "actor_id",
            _validate_event_local_id(actor_id, "actor id"),
        )

    @classmethod
    def from_dict(cls, value: object) -> EventActorRef:
        payload = _require_exact_object(
            value,
            fields=_ACTOR_FIELDS,
            field_name="actor",
        )
        return cls(payload["kind"], payload["id"])

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.actor_id}


@dataclass(frozen=True, slots=True, init=False)
class EventEntityRef:
    """Caller-declared subject reference for one Event occurrence."""

    kind: str
    entity_id: str

    def __init__(self, kind: str, entity_id: str) -> None:
        object.__setattr__(
            self,
            "kind",
            _validate_enum(
                kind,
                field_name="entity kind",
                supported=_ENTITY_KINDS,
            ),
        )
        object.__setattr__(
            self,
            "entity_id",
            _validate_event_local_id(entity_id, "entity id"),
        )

    @classmethod
    def from_dict(cls, value: object) -> EventEntityRef:
        payload = _require_exact_object(
            value,
            fields=_ENTITY_FIELDS,
            field_name="entity_ref",
        )
        return cls(payload["kind"], payload["id"])

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.entity_id}


@dataclass(frozen=True, slots=True, init=False)
class EventDefinitionRef:
    """Role-qualified immutable definition identity embedded in an Event."""

    role: str
    definition_id: str
    version: str
    digest: str

    def __init__(
        self,
        role: str,
        definition_id: str,
        version: str,
        digest: str,
    ) -> None:
        normalized_role = _validate_enum(
            role,
            field_name="definition ref role",
            supported=_DEFINITION_ROLES,
        )
        normalized_id, normalized_version, normalized_digest = (
            _validate_definition_parts(definition_id, version, digest)
        )
        object.__setattr__(self, "role", normalized_role)
        object.__setattr__(self, "definition_id", normalized_id)
        object.__setattr__(self, "version", normalized_version)
        object.__setattr__(self, "digest", normalized_digest)

    @classmethod
    def from_dict(cls, value: object) -> EventDefinitionRef:
        payload = _require_exact_object(
            value,
            fields=_DEFINITION_REF_FIELDS,
            field_name="definition ref",
        )
        return cls(
            payload["role"],
            payload["id"],
            payload["version"],
            payload["digest"],
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "id": self.definition_id,
            "version": self.version,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True, init=False)
class EventRedactionSummary:
    """Caller-declared redaction summary; it is not redaction proof."""

    status: str
    paths: tuple[str, ...]

    def __init__(
        self,
        status: str,
        paths: list[str] | tuple[str, ...],
    ) -> None:
        normalized_status = _validate_enum(
            status,
            field_name="redaction status",
            supported=_REDACTION_STATUSES,
        )
        if type(paths) not in (list, tuple):
            raise _invalid_event_envelope(
                "redaction paths must be a list of JSON pointers"
            )
        if len(paths) > _REDACTION_PATHS_MAX_ITEMS:
            raise _invalid_event_envelope(
                "redaction paths must contain at most 64 items"
            )
        normalized_paths: list[str] = []
        for path in paths:
            if type(path) is not str or len(path) > _JSON_POINTER_MAX_LENGTH:
                raise _invalid_event_envelope(
                    "redaction paths must contain valid JSON pointers"
                )
            try:
                path.encode("utf-8")
            except UnicodeError:
                raise _invalid_event_envelope(
                    "redaction paths must contain valid JSON pointers"
                ) from None
            if not _JSON_POINTER.fullmatch(path):
                raise _invalid_event_envelope(
                    "redaction paths must contain valid JSON pointers"
                )
            normalized_paths.append(path)
        if len(set(normalized_paths)) != len(normalized_paths):
            raise _invalid_event_envelope(
                "redaction paths must not contain duplicates"
            )
        if normalized_status == "not_applied" and normalized_paths:
            raise _invalid_event_envelope(
                "not_applied redaction requires empty paths"
            )
        if normalized_status == "applied" and not normalized_paths:
            raise _invalid_event_envelope(
                "applied redaction requires at least one path"
            )
        object.__setattr__(self, "status", normalized_status)
        object.__setattr__(self, "paths", tuple(sorted(normalized_paths)))

    @classmethod
    def from_dict(cls, value: object) -> EventRedactionSummary:
        payload = _require_exact_object(
            value,
            fields=_REDACTION_FIELDS,
            field_name="redaction",
        )
        if type(payload["paths"]) is not list:
            raise _invalid_event_envelope(
                "redaction paths must be a JSON array"
            )
        return cls(payload["status"], payload["paths"])

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "paths": list(self.paths)}


@dataclass(frozen=True, slots=True, init=False)
class EventEnvelope:
    """Immutable, canonical PactTrail Event occurrence schema v1."""

    event_id: str
    event_type: str
    run_id: str
    sequence: int
    occurred_at: str
    recorded_at: str
    actor: EventActorRef
    entity_ref: EventEntityRef
    correlation_id: str | None
    causation_id: str | None
    definition_refs: tuple[EventDefinitionRef, ...]
    payload_schema_version: int
    _payload_json: str = field(repr=False)
    redaction: EventRedactionSummary
    _canonical_json: str = field(repr=False)
    _digest: str = field(repr=False)

    def __init__(
        self,
        event_id: str,
        event_type: str,
        run_id: str,
        sequence: int,
        occurred_at: str,
        recorded_at: str,
        actor: EventActorRef,
        entity_ref: EventEntityRef,
        correlation_id: str | None,
        causation_id: str | None,
        definition_refs: list[EventDefinitionRef]
        | tuple[EventDefinitionRef, ...],
        payload_schema_version: int,
        payload: dict[str, object],
        redaction: EventRedactionSummary,
    ) -> None:
        normalized_event_id = _validate_event_local_id(event_id, "event_id")
        normalized_event_type = _validate_enum(
            event_type,
            field_name="event_type",
            supported=_EVENT_TYPES,
        )
        normalized_run_id = _validate_run_id(run_id)
        normalized_sequence = _validate_sequence(sequence)
        normalized_occurred_at = _validate_timestamp(
            occurred_at,
            "occurred_at",
        )
        normalized_recorded_at = _validate_timestamp(
            recorded_at,
            "recorded_at",
        )

        if type(actor) is not EventActorRef:
            raise _invalid_event_envelope(
                "actor must be an exact EventActorRef"
            )
        try:
            normalized_actor = EventActorRef(actor.kind, actor.actor_id)
        except AttributeError:
            raise _invalid_event_envelope(
                "actor must be a valid exact EventActorRef"
            ) from None

        if type(entity_ref) is not EventEntityRef:
            raise _invalid_event_envelope(
                "entity_ref must be an exact EventEntityRef"
            )
        try:
            normalized_entity = EventEntityRef(
                entity_ref.kind,
                entity_ref.entity_id,
            )
        except AttributeError:
            raise _invalid_event_envelope(
                "entity_ref must be a valid exact EventEntityRef"
            ) from None

        expected_entity_kind = _EVENT_ENTITY_KIND_BY_TYPE.get(
            normalized_event_type
        )
        if expected_entity_kind is None:
            raise _invalid_event_envelope(
                "event_type has no supported entity kind"
            )
        if normalized_entity.kind != expected_entity_kind:
            raise _invalid_event_envelope(
                "entity kind does not match event_type"
            )
        if (
            expected_entity_kind == "run"
            and normalized_entity.entity_id != normalized_run_id
        ):
            raise _invalid_event_envelope(
                "run event entity id must equal run_id"
            )

        normalized_correlation_id = _validate_nullable_event_local_id(
            correlation_id,
            "correlation_id",
        )
        normalized_causation_id = _validate_nullable_event_local_id(
            causation_id,
            "causation_id",
        )
        if normalized_causation_id == normalized_event_id:
            raise _invalid_event_envelope(
                "causation_id must not equal event_id"
            )

        normalized_refs = _normalize_definition_refs(definition_refs)
        normalized_payload_version = _validate_payload_schema_version(
            payload_schema_version
        )
        payload_json = _canonicalize_payload(payload)

        if type(redaction) is not EventRedactionSummary:
            raise _invalid_event_envelope(
                "redaction must be an exact EventRedactionSummary"
            )
        try:
            normalized_redaction = EventRedactionSummary(
                redaction.status,
                redaction.paths,
            )
        except AttributeError:
            raise _invalid_event_envelope(
                "redaction must be a valid exact EventRedactionSummary"
            ) from None

        canonical_payload: dict[str, object] = {
            "schema_version": _EVENT_ENVELOPE_SCHEMA_VERSION,
            "event_id": normalized_event_id,
            "event_type": normalized_event_type,
            "run_id": normalized_run_id,
            "sequence": normalized_sequence,
            "occurred_at": normalized_occurred_at,
            "recorded_at": normalized_recorded_at,
            "actor": normalized_actor.to_dict(),
            "entity_ref": normalized_entity.to_dict(),
            "correlation_id": normalized_correlation_id,
            "causation_id": normalized_causation_id,
            "definition_refs": [item.to_dict() for item in normalized_refs],
            "payload_schema_version": normalized_payload_version,
            "payload": json.loads(payload_json),
            "redaction": normalized_redaction.to_dict(),
        }
        try:
            canonical_json = serialize_canonical_json(canonical_payload)
            digest = canonical_sha256(canonical_payload)
        except PactTrailError:
            raise _invalid_event_envelope(
                "envelope must contain bounded canonical JSON values"
            ) from None
        if len(canonical_json.encode("utf-8")) > _ENVELOPE_MAX_BYTES:
            raise _invalid_event_envelope(
                "envelope must contain at most 327680 canonical bytes"
            )

        object.__setattr__(self, "event_id", normalized_event_id)
        object.__setattr__(self, "event_type", normalized_event_type)
        object.__setattr__(self, "run_id", normalized_run_id)
        object.__setattr__(self, "sequence", normalized_sequence)
        object.__setattr__(self, "occurred_at", normalized_occurred_at)
        object.__setattr__(self, "recorded_at", normalized_recorded_at)
        object.__setattr__(self, "actor", normalized_actor)
        object.__setattr__(self, "entity_ref", normalized_entity)
        object.__setattr__(
            self,
            "correlation_id",
            normalized_correlation_id,
        )
        object.__setattr__(self, "causation_id", normalized_causation_id)
        object.__setattr__(self, "definition_refs", normalized_refs)
        object.__setattr__(
            self,
            "payload_schema_version",
            normalized_payload_version,
        )
        object.__setattr__(self, "_payload_json", payload_json)
        object.__setattr__(self, "redaction", normalized_redaction)
        object.__setattr__(self, "_canonical_json", canonical_json)
        object.__setattr__(self, "_digest", digest)

    @property
    def schema_version(self) -> str:
        return _EVENT_ENVELOPE_SCHEMA_VERSION

    @property
    def payload(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self._payload_json))

    @classmethod
    def from_dict(cls, value: object) -> EventEnvelope:
        envelope = _require_exact_object(
            value,
            fields=_EVENT_ENVELOPE_FIELDS,
            field_name="event envelope",
        )
        if (
            type(envelope["schema_version"]) is not str
            or envelope["schema_version"] != _EVENT_ENVELOPE_SCHEMA_VERSION
        ):
            raise _invalid_event_envelope(
                "schema_version must be pacttrail.event-envelope.v1"
            )
        return cls(
            event_id=envelope["event_id"],
            event_type=envelope["event_type"],
            run_id=envelope["run_id"],
            sequence=envelope["sequence"],
            occurred_at=envelope["occurred_at"],
            recorded_at=envelope["recorded_at"],
            actor=EventActorRef.from_dict(envelope["actor"]),
            entity_ref=EventEntityRef.from_dict(envelope["entity_ref"]),
            correlation_id=envelope["correlation_id"],
            causation_id=envelope["causation_id"],
            definition_refs=_parse_definition_refs(
                envelope["definition_refs"]
            ),
            payload_schema_version=envelope["payload_schema_version"],
            payload=envelope["payload"],
            redaction=EventRedactionSummary.from_dict(envelope["redaction"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _EVENT_ENVELOPE_SCHEMA_VERSION,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            "recorded_at": self.recorded_at,
            "actor": self.actor.to_dict(),
            "entity_ref": self.entity_ref.to_dict(),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "definition_refs": [item.to_dict() for item in self.definition_refs],
            "payload_schema_version": self.payload_schema_version,
            "payload": self.payload,
            "redaction": self.redaction.to_dict(),
        }

    def canonical_payload(self) -> dict[str, object]:
        return self.to_dict()

    def canonical_json(self) -> str:
        return self._canonical_json

    @property
    def digest(self) -> str:
        return self._digest

    @property
    def identity(self) -> EventIdentity:
        return EventIdentity(self.event_id, self.digest)
