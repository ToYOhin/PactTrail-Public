from __future__ import annotations

from dataclasses import dataclass

from .definitions import (
    DefinitionRef,
    canonical_json as serialize_canonical_json,
    canonical_sha256,
    validate_definition_id,
    validate_semantic_version,
    validate_sha256,
)
from .runs import PactTrailError


_POLICY_DEFINITION_SCHEMA_VERSION = "pacttrail.policy.v1"
POLICY_DEFINITION_SCHEMA_VERSION = _POLICY_DEFINITION_SCHEMA_VERSION

_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "policy_id",
        "version",
        "description",
        "priority",
        "default_result",
        "rules",
    }
)
_RULE_FIELDS = frozenset(
    {
        "rule_id",
        "priority",
        "result",
        "reason_code",
        "subjects",
        "tool_refs",
        "risk_levels",
        "side_effects",
        "required_scopes",
        "data_labels",
    }
)
_DEFINITION_REF_FIELDS = frozenset({"id", "version", "digest"})
_RULE_RESULTS = frozenset({"allow", "deny", "require_approval"})
_DEFAULT_RESULTS = frozenset({"deny"})
_RISK_LEVELS = frozenset({"L0", "L1", "L2", "L3"})
_SIDE_EFFECTS = frozenset(
    {"none", "recoverable", "external", "irreversible"}
)
_STABLE_ID_MAX_LENGTH = 128
_VERSION_MAX_LENGTH = 128
_DESCRIPTION_MAX_LENGTH = 4096
_PRIORITY_MAX = 1000
_RULES_MAX_ITEMS = 64
_SELECTOR_MAX_ITEMS = 64
_POLICY_MAX_SELECTOR_ITEMS = 4096
_POLICY_MAX_BYTES = 1048576


def _invalid_policy_definition(reason: str) -> PactTrailError:
    return PactTrailError(f"invalid_policy_definition: {reason}")


def _validate_exact_mapping(
    value: object,
    *,
    fields: frozenset[str],
    label: str,
) -> dict[str, object]:
    if type(value) is not dict:
        raise _invalid_policy_definition(f"{label} must be a JSON object")
    if len(value) > len(fields):
        raise _invalid_policy_definition(f"{label} contains unknown fields")
    if any(type(key) is not str for key in value):
        raise _invalid_policy_definition(
            f"{label} keys must be plain JSON strings"
        )
    keys = set(value)
    if keys - fields:
        raise _invalid_policy_definition(f"{label} contains unknown fields")
    if fields - keys:
        raise _invalid_policy_definition(
            f"{label} is missing required fields"
        )
    return value


def _validate_stable_id(value: object, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) > _STABLE_ID_MAX_LENGTH
        or not value.isascii()
    ):
        raise _invalid_policy_definition(
            f"{field_name} must be a valid lower-case stable id"
        )
    try:
        validate_definition_id(value)
    except PactTrailError:
        raise _invalid_policy_definition(
            f"{field_name} must be a valid lower-case stable id"
        ) from None
    return value


def _validate_version(value: object) -> str:
    if (
        type(value) is not str
        or len(value) > _VERSION_MAX_LENGTH
        or not value.isascii()
    ):
        raise _invalid_policy_definition(
            "version must be an exact bounded semantic version"
        )
    try:
        validate_semantic_version(value)
    except PactTrailError:
        raise _invalid_policy_definition(
            "version must be an exact bounded semantic version"
        ) from None
    return value


def _validate_description(value: object) -> str:
    if type(value) is not str:
        raise _invalid_policy_definition("description must be text")
    if len(value) > _DESCRIPTION_MAX_LENGTH:
        raise _invalid_policy_definition(
            "description must contain at most 4096 characters"
        )
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _invalid_policy_definition(
            "description must be valid UTF-8 text"
        ) from None
    if not value.strip():
        raise _invalid_policy_definition("description must not be empty")
    return value


def _validate_priority(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise _invalid_policy_definition(
            f"{field_name} must be a plain integer"
        )
    if not 0 <= value <= _PRIORITY_MAX:
        raise _invalid_policy_definition(
            f"{field_name} must be between 0 and 1000"
        )
    return value


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
        raise _invalid_policy_definition(
            f"{field_name} contains an unsupported value"
        )
    return value


def _normalize_stable_ids(
    value: object,
    field_name: str,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if type(value) not in (list, tuple):
        raise _invalid_policy_definition(
            f"{field_name} must be null or a list of stable ids"
        )
    if not value:
        raise _invalid_policy_definition(
            f"{field_name} must not be an empty list"
        )
    if len(value) > _SELECTOR_MAX_ITEMS:
        raise _invalid_policy_definition(
            f"{field_name} must contain at most 64 items"
        )
    normalized = tuple(
        _validate_stable_id(item, field_name) for item in value
    )
    if len(set(normalized)) != len(normalized):
        raise _invalid_policy_definition(
            f"{field_name} must not contain duplicates"
        )
    return tuple(sorted(normalized))


def _normalize_enum_selector(
    value: object,
    *,
    field_name: str,
    supported: frozenset[str],
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if type(value) not in (list, tuple):
        raise _invalid_policy_definition(
            f"{field_name} must be null or a list"
        )
    if not value:
        raise _invalid_policy_definition(
            f"{field_name} must not be an empty list"
        )
    if len(value) > _SELECTOR_MAX_ITEMS:
        raise _invalid_policy_definition(
            f"{field_name} must contain at most 64 items"
        )
    normalized = tuple(
        _validate_enum(
            item,
            field_name=field_name,
            supported=supported,
        )
        for item in value
    )
    if len(set(normalized)) != len(normalized):
        raise _invalid_policy_definition(
            f"{field_name} must not contain duplicates"
        )
    return tuple(sorted(normalized))


def _validate_definition_ref(value: object) -> DefinitionRef:
    if type(value) is not DefinitionRef:
        raise _invalid_policy_definition(
            "tool_refs must contain exact DefinitionRef values"
        )
    try:
        parts = (value.definition_id, value.version, value.digest)
    except AttributeError:
        raise _invalid_policy_definition(
            "tool_refs must contain valid exact definition refs"
        ) from None
    if any(type(part) is not str for part in parts):
        raise _invalid_policy_definition(
            "tool_refs must contain plain JSON strings"
        )
    try:
        if (
            len(parts[0]) > _STABLE_ID_MAX_LENGTH
            or not parts[0].isascii()
        ):
            raise PactTrailError("bounded ref id required")
        validate_definition_id(parts[0])
        if (
            len(parts[1]) > _VERSION_MAX_LENGTH
            or not parts[1].isascii()
        ):
            raise PactTrailError("bounded ref version required")
        validate_semantic_version(parts[1])
        validate_sha256(parts[2])
    except PactTrailError:
        raise _invalid_policy_definition(
            "tool_refs must contain valid exact definition refs"
        ) from None
    return DefinitionRef(parts[0], parts[1], parts[2])


def _parse_definition_ref(value: object) -> DefinitionRef:
    mapping = _validate_exact_mapping(
        value,
        fields=_DEFINITION_REF_FIELDS,
        label="tool ref",
    )
    if any(
        type(mapping[field_name]) is not str
        for field_name in _DEFINITION_REF_FIELDS
    ):
        raise _invalid_policy_definition(
            "tool ref must contain plain JSON strings"
        )
    try:
        if (
            len(mapping["id"]) > _STABLE_ID_MAX_LENGTH
            or not mapping["id"].isascii()
            or len(mapping["version"]) > _VERSION_MAX_LENGTH
            or not mapping["version"].isascii()
        ):
            raise PactTrailError("bounded ref parts required")
        return DefinitionRef(
            definition_id=mapping["id"],
            version=mapping["version"],
            digest=mapping["digest"],
        )
    except PactTrailError:
        raise _invalid_policy_definition(
            "tool ref must contain a valid exact definition ref"
        ) from None


def _normalize_tool_refs(
    value: object,
) -> tuple[DefinitionRef, ...] | None:
    if value is None:
        return None
    if type(value) not in (list, tuple):
        raise _invalid_policy_definition(
            "tool_refs must be null or a list of exact refs"
        )
    if not value:
        raise _invalid_policy_definition(
            "tool_refs must not be an empty list"
        )
    if len(value) > _SELECTOR_MAX_ITEMS:
        raise _invalid_policy_definition(
            "tool_refs must contain at most 64 items"
        )
    normalized = tuple(_validate_definition_ref(item) for item in value)
    identities = tuple(
        (item.definition_id, item.version, item.digest)
        for item in normalized
    )
    if len(set(identities)) != len(identities):
        raise _invalid_policy_definition(
            "tool_refs must not contain duplicates"
        )
    version_digests: dict[tuple[str, str], str] = {}
    for definition_id, version, digest in identities:
        key = definition_id, version
        known_digest = version_digests.setdefault(key, digest)
        if known_digest != digest:
            raise _invalid_policy_definition(
                "tool_refs contain conflicting exact versions"
            )
    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                item.definition_id,
                item.version,
                item.digest,
            ),
        )
    )


def _parse_tool_refs(value: object) -> list[DefinitionRef] | None:
    if value is None:
        return None
    if type(value) is not list:
        raise _invalid_policy_definition(
            "tool_refs must be null or a JSON array"
        )
    if not value:
        raise _invalid_policy_definition(
            "tool_refs must not be an empty list"
        )
    if len(value) > _SELECTOR_MAX_ITEMS:
        raise _invalid_policy_definition(
            "tool_refs must contain at most 64 items"
        )
    return [_parse_definition_ref(item) for item in value]


def _require_reader_selector(value: object, field_name: str) -> None:
    if value is not None and type(value) is not list:
        raise _invalid_policy_definition(
            f"{field_name} must be null or a JSON array"
        )


@dataclass(frozen=True, slots=True, init=False)
class PolicyRule:
    """Immutable declaration of one closed Policy rule."""

    rule_id: str
    priority: int
    result: str
    reason_code: str
    subjects: tuple[str, ...] | None
    tool_refs: tuple[DefinitionRef, ...] | None
    risk_levels: tuple[str, ...] | None
    side_effects: tuple[str, ...] | None
    required_scopes: tuple[str, ...] | None
    data_labels: tuple[str, ...] | None

    def __init__(
        self,
        rule_id: str,
        priority: int,
        result: str,
        reason_code: str,
        subjects: list[str] | tuple[str, ...] | None,
        tool_refs: list[DefinitionRef] | tuple[DefinitionRef, ...] | None,
        risk_levels: list[str] | tuple[str, ...] | None,
        side_effects: list[str] | tuple[str, ...] | None,
        required_scopes: list[str] | tuple[str, ...] | None,
        data_labels: list[str] | tuple[str, ...] | None,
    ) -> None:
        normalized_rule_id = _validate_stable_id(rule_id, "rule_id")
        normalized_priority = _validate_priority(priority, "rule priority")
        normalized_result = _validate_enum(
            result,
            field_name="result",
            supported=_RULE_RESULTS,
        )
        normalized_reason = _validate_stable_id(
            reason_code,
            "reason_code",
        )
        normalized_subjects = _normalize_stable_ids(subjects, "subjects")
        normalized_tool_refs = _normalize_tool_refs(tool_refs)
        normalized_risks = _normalize_enum_selector(
            risk_levels,
            field_name="risk_levels",
            supported=_RISK_LEVELS,
        )
        normalized_effects = _normalize_enum_selector(
            side_effects,
            field_name="side_effects",
            supported=_SIDE_EFFECTS,
        )
        normalized_scopes = _normalize_stable_ids(
            required_scopes,
            "required_scopes",
        )
        normalized_labels = _normalize_stable_ids(
            data_labels,
            "data_labels",
        )
        if all(
            selector is None
            for selector in (
                normalized_subjects,
                normalized_tool_refs,
                normalized_risks,
                normalized_effects,
                normalized_scopes,
                normalized_labels,
            )
        ):
            raise _invalid_policy_definition(
                "rule must declare at least one selector"
            )

        object.__setattr__(self, "rule_id", normalized_rule_id)
        object.__setattr__(self, "priority", normalized_priority)
        object.__setattr__(self, "result", normalized_result)
        object.__setattr__(self, "reason_code", normalized_reason)
        object.__setattr__(self, "subjects", normalized_subjects)
        object.__setattr__(self, "tool_refs", normalized_tool_refs)
        object.__setattr__(self, "risk_levels", normalized_risks)
        object.__setattr__(self, "side_effects", normalized_effects)
        object.__setattr__(self, "required_scopes", normalized_scopes)
        object.__setattr__(self, "data_labels", normalized_labels)

    @classmethod
    def from_dict(cls, value: object) -> PolicyRule:
        mapping = _validate_exact_mapping(
            value,
            fields=_RULE_FIELDS,
            label="rule",
        )
        for field_name in (
            "subjects",
            "risk_levels",
            "side_effects",
            "required_scopes",
            "data_labels",
        ):
            _require_reader_selector(mapping[field_name], field_name)
        return cls(
            rule_id=mapping["rule_id"],
            priority=mapping["priority"],
            result=mapping["result"],
            reason_code=mapping["reason_code"],
            subjects=mapping["subjects"],
            tool_refs=_parse_tool_refs(mapping["tool_refs"]),
            risk_levels=mapping["risk_levels"],
            side_effects=mapping["side_effects"],
            required_scopes=mapping["required_scopes"],
            data_labels=mapping["data_labels"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "priority": self.priority,
            "result": self.result,
            "reason_code": self.reason_code,
            "subjects": (
                list(self.subjects) if self.subjects is not None else None
            ),
            "tool_refs": (
                [reference.to_dict() for reference in self.tool_refs]
                if self.tool_refs is not None
                else None
            ),
            "risk_levels": (
                list(self.risk_levels)
                if self.risk_levels is not None
                else None
            ),
            "side_effects": (
                list(self.side_effects)
                if self.side_effects is not None
                else None
            ),
            "required_scopes": (
                list(self.required_scopes)
                if self.required_scopes is not None
                else None
            ),
            "data_labels": (
                list(self.data_labels)
                if self.data_labels is not None
                else None
            ),
        }


def _clone_policy_rule(value: object) -> PolicyRule:
    if type(value) is not PolicyRule:
        raise _invalid_policy_definition(
            "rules must contain exact PolicyRule values"
        )
    try:
        return PolicyRule(
            rule_id=value.rule_id,
            priority=value.priority,
            result=value.result,
            reason_code=value.reason_code,
            subjects=value.subjects,
            tool_refs=value.tool_refs,
            risk_levels=value.risk_levels,
            side_effects=value.side_effects,
            required_scopes=value.required_scopes,
            data_labels=value.data_labels,
        )
    except AttributeError:
        raise _invalid_policy_definition(
            "rules must contain valid PolicyRule values"
        ) from None


def _normalize_rules(value: object) -> tuple[PolicyRule, ...]:
    if type(value) not in (list, tuple):
        raise _invalid_policy_definition(
            "rules must be a list of PolicyRule values"
        )
    if len(value) > _RULES_MAX_ITEMS:
        raise _invalid_policy_definition("rules must contain at most 64 items")

    normalized: list[PolicyRule] = []
    seen_ids: set[str] = set()
    selector_items = 0
    for item in value:
        rule = _clone_policy_rule(item)
        if rule.rule_id in seen_ids:
            raise _invalid_policy_definition(
                "rules must have unique rule_id values"
            )
        seen_ids.add(rule.rule_id)
        selector_items += sum(
            len(selector) if selector is not None else 0
            for selector in (
                rule.subjects,
                rule.tool_refs,
                rule.risk_levels,
                rule.side_effects,
                rule.required_scopes,
                rule.data_labels,
            )
        )
        if selector_items > _POLICY_MAX_SELECTOR_ITEMS:
            raise _invalid_policy_definition(
                "policy contains too many selector items"
            )
        normalized.append(rule)
    return tuple(
        sorted(normalized, key=lambda rule: (-rule.priority, rule.rule_id))
    )


@dataclass(frozen=True, slots=True, init=False)
class PolicyDefinition:
    """Immutable, canonical PactTrail Policy declaration schema v1."""

    policy_id: str
    version: str
    description: str
    priority: int
    default_result: str
    rules: tuple[PolicyRule, ...]

    def __init__(
        self,
        policy_id: str,
        version: str,
        description: str,
        priority: int,
        default_result: str,
        rules: list[PolicyRule] | tuple[PolicyRule, ...],
    ) -> None:
        normalized_policy_id = _validate_stable_id(policy_id, "policy_id")
        normalized_version = _validate_version(version)
        normalized_description = _validate_description(description)
        normalized_priority = _validate_priority(priority, "policy priority")
        normalized_default = _validate_enum(
            default_result,
            field_name="default_result",
            supported=_DEFAULT_RESULTS,
        )
        normalized_rules = _normalize_rules(rules)

        payload = {
            "schema_version": _POLICY_DEFINITION_SCHEMA_VERSION,
            "policy_id": normalized_policy_id,
            "version": normalized_version,
            "description": normalized_description,
            "priority": normalized_priority,
            "default_result": normalized_default,
            "rules": [rule.to_dict() for rule in normalized_rules],
        }
        try:
            encoded = serialize_canonical_json(payload).encode("utf-8")
        except PactTrailError:
            raise _invalid_policy_definition(
                "payload must contain bounded canonical JSON values"
            ) from None
        if len(encoded) > _POLICY_MAX_BYTES:
            raise _invalid_policy_definition(
                "canonical payload must contain at most 1048576 bytes"
            )

        object.__setattr__(self, "policy_id", normalized_policy_id)
        object.__setattr__(self, "version", normalized_version)
        object.__setattr__(self, "description", normalized_description)
        object.__setattr__(self, "priority", normalized_priority)
        object.__setattr__(self, "default_result", normalized_default)
        object.__setattr__(self, "rules", normalized_rules)

    @property
    def schema_version(self) -> str:
        return _POLICY_DEFINITION_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, value: object) -> PolicyDefinition:
        mapping = _validate_exact_mapping(
            value,
            fields=_POLICY_FIELDS,
            label="payload",
        )
        if (
            type(mapping["schema_version"]) is not str
            or mapping["schema_version"] != _POLICY_DEFINITION_SCHEMA_VERSION
        ):
            raise _invalid_policy_definition(
                "schema_version must be pacttrail.policy.v1"
            )
        if type(mapping["rules"]) is not list:
            raise _invalid_policy_definition("rules must be a JSON array")
        if len(mapping["rules"]) > _RULES_MAX_ITEMS:
            raise _invalid_policy_definition(
                "rules must contain at most 64 items"
            )
        rules = [PolicyRule.from_dict(item) for item in mapping["rules"]]
        return cls(
            policy_id=mapping["policy_id"],
            version=mapping["version"],
            description=mapping["description"],
            priority=mapping["priority"],
            default_result=mapping["default_result"],
            rules=rules,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _POLICY_DEFINITION_SCHEMA_VERSION,
            "policy_id": self.policy_id,
            "version": self.version,
            "description": self.description,
            "priority": self.priority,
            "default_result": self.default_result,
            "rules": [rule.to_dict() for rule in self.rules],
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
        return DefinitionRef(self.policy_id, self.version, self.digest)
