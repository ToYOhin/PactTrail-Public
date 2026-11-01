from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import json
from typing import Any

from .definitions import (
    canonical_json,
    canonical_sha256,
    validate_definition_id,
    validate_semantic_version,
    validate_sha256,
)
from .runs import PactTrailError, validate_run_id
from .workflows import FlowDefinition


RUN_BINDING_SCHEMA_VERSION = "pacttrail.run-binding.v1"


@dataclass(frozen=True, slots=True)
class RunBinding:
    run_id: str
    flow_id: str
    flow_version: str
    flow_digest: str
    canonical_flow_definition: dict[str, Any]
    created_at: str
    schema_version: str = RUN_BINDING_SCHEMA_VERSION

    @classmethod
    def for_flow(
        cls,
        run_id: str,
        definition: FlowDefinition,
        *,
        created_at: datetime | None = None,
    ) -> RunBinding:
        canonical_definition = definition.canonical_payload()
        timestamp = created_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise PactTrailError("run binding created_at must be UTC")
        return cls(
            run_id=validate_run_id(run_id),
            flow_id=definition.flow_id,
            flow_version=definition.version,
            flow_digest=canonical_sha256(canonical_definition),
            canonical_flow_definition=_copy_json(canonical_definition),
            created_at=timestamp.isoformat(),
        )

    @classmethod
    def from_dict(cls, value: Any) -> RunBinding:
        if not isinstance(value, dict):
            raise PactTrailError("run binding must be a JSON object")
        schema_version = _required_string(value, "schema_version")
        if schema_version != RUN_BINDING_SCHEMA_VERSION:
            raise PactTrailError(f"unsupported run binding schema: {schema_version}")
        run_id = validate_run_id(_required_string(value, "run_id"))
        flow_id = validate_definition_id(_required_string(value, "flow_id"))
        flow_version = validate_semantic_version(
            _required_string(value, "flow_version")
        )
        flow_digest = validate_sha256(_required_string(value, "flow_digest"))
        created_at = _required_string(value, "created_at")
        _validate_created_at(created_at)

        canonical_definition = value.get("canonical_flow_definition")
        if not isinstance(canonical_definition, dict):
            raise PactTrailError(
                "run binding canonical_flow_definition must be a JSON object"
            )
        if canonical_definition.get("schema_version") != "pacttrail.flow.v1":
            raise PactTrailError("run binding contains an unsupported flow schema")
        if canonical_definition.get("flow_id") != flow_id:
            raise PactTrailError("run binding flow_id does not match its definition")
        if canonical_definition.get("version") != flow_version:
            raise PactTrailError("run binding flow_version does not match its definition")
        actual_digest = canonical_sha256(canonical_definition)
        if not hmac.compare_digest(actual_digest, flow_digest):
            raise PactTrailError("run binding flow digest does not match its definition")

        return cls(
            run_id=run_id,
            flow_id=flow_id,
            flow_version=flow_version,
            flow_digest=flow_digest,
            canonical_flow_definition=_copy_json(canonical_definition),
            created_at=created_at,
            schema_version=schema_version,
        )

    def verify_definition(self, definition: FlowDefinition) -> None:
        try:
            supplied_definition = definition.canonical_payload()
        except PactTrailError as exc:
            raise PactTrailError(
                f"definition_mismatch: supplied Flow is not canonical: {exc}"
            ) from exc
        supplied_digest = canonical_sha256(supplied_definition)
        if (
            definition.flow_id != self.flow_id
            or definition.version != self.flow_version
            or not hmac.compare_digest(supplied_digest, self.flow_digest)
            or canonical_json(supplied_definition)
            != canonical_json(self.canonical_flow_definition)
        ):
            raise PactTrailError(
                "definition_mismatch: "
                f"run {self.run_id} is bound to "
                f"{self.flow_id}@{self.flow_version}#{self.flow_digest[:12]}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "flow_id": self.flow_id,
            "flow_version": self.flow_version,
            "flow_digest": self.flow_digest,
            "canonical_flow_definition": _copy_json(
                self.canonical_flow_definition
            ),
            "created_at": self.created_at,
        }


def _required_string(value: dict[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise PactTrailError(f"run binding {field} must be a non-empty string")
    return result


def _validate_created_at(value: str) -> None:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PactTrailError("run binding created_at must be ISO 8601") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise PactTrailError("run binding created_at must be UTC")


def _copy_json(value: Any) -> Any:
    return json.loads(canonical_json(value))
