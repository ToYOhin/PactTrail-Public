from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import math
from typing import NoReturn

from pacttrail.domain.runs import (
    PactTrailError,
    RunStatus,
    StepSnapshot,
    validate_run_id,
)
from pacttrail.domain.workflows import FlowDefinition, FlowStepDefinition


_STATUS_MAP = {
    "pending": RunStatus.QUEUED,
    "running": RunStatus.RUNNING,
    "waiting_approval": RunStatus.WAITING_APPROVAL,
    "success": RunStatus.SUCCEEDED,
    "failed": RunStatus.FAILED,
    "skipped": RunStatus.CANCELED,
}
_STEP_APPROVAL_DECISIONS = {"pending", "approve", "reject"}
_STORED_APPROVAL_ACTIONS = {"approve", "reject"}


@dataclass(frozen=True, slots=True)
class ValidatedCheckpointRecord:
    run_id: str
    status: RunStatus
    upstream_status: str
    error: str | None
    steps: tuple[StepSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _ValidatedApprovalRecord:
    action: str
    request_id: str


def parse_checkpoint_record(
    record: object,
    *,
    requested_run_id: str,
    definition: FlowDefinition,
) -> ValidatedCheckpointRecord:
    if not isinstance(record, Mapping):
        _invalid("record must be an object")

    record_run_id = _required_text(_field(record, "run_id", "record"), "run_id")
    try:
        validate_run_id(record_run_id)
    except PactTrailError:
        _invalid("run_id is invalid")
    if record_run_id != requested_run_id:
        _invalid("run_id does not match the requested run")

    upstream_status, status = _parse_status(
        _field(record, "status", "record"),
        "status",
    )
    _required_string(_field(record, "query", "record"), "query")
    error = _optional_text(_field(record, "error", "record"), "error")
    raw_approvals = _field(record, "approvals", "record")
    if not isinstance(raw_approvals, Mapping):
        _invalid("approvals must be an object")
    _finite_number(
        _field(record, "updated_at", "record"),
        "updated_at",
        non_negative=True,
    )
    raw_steps = _field(record, "steps", "record")
    if not isinstance(raw_steps, list):
        _invalid("steps must be a list")

    expected_names = _expected_step_names(definition)
    definitions_by_name = {step.name: step for step in definition.steps}
    seen: set[str] = set()
    steps: list[StepSnapshot] = []
    for index, raw_step in enumerate(raw_steps):
        path = f"steps[{index}]"
        if not isinstance(raw_step, Mapping):
            _invalid(f"{path} must be an object")
        name = _required_text(_field(raw_step, "name", path), f"{path}.name")
        if name in seen:
            _invalid("step names must be unique")
        if name not in definitions_by_name:
            _invalid(f"{path}.name is not part of the bound Flow")
        seen.add(name)

        step_status_text, step_status = _parse_status(
            _field(raw_step, "status", path),
            f"{path}.status",
        )
        attempts = _non_negative_int(
            _field(raw_step, "attempts", path),
            f"{path}.attempts",
        )
        trace = _field(raw_step, "trace", path)
        if not isinstance(trace, list) or any(
            not isinstance(event, Mapping) for event in trace
        ):
            _invalid(f"{path}.trace must be a list of objects")
        _optional_finite_number(
            _field(raw_step, "started_at", path),
            f"{path}.started_at",
            non_negative=True,
        )
        _optional_finite_number(
            _field(raw_step, "ended_at", path),
            f"{path}.ended_at",
            non_negative=True,
        )
        _optional_finite_number(
            _field(raw_step, "duration_ms", path),
            f"{path}.duration_ms",
            non_negative=True,
        )
        _optional_text(
            _field(raw_step, "input_summary", path),
            f"{path}.input_summary",
        )
        _optional_text(
            _field(raw_step, "output_summary", path),
            f"{path}.output_summary",
        )
        _non_negative_int(
            _field(raw_step, "retry_count", path),
            f"{path}.retry_count",
        )
        used_fallback = _field(raw_step, "used_fallback", path)
        if not isinstance(used_fallback, bool):
            _invalid(f"{path}.used_fallback must be a boolean")
        content = _required_string(
            _field(raw_step, "content", path),
            f"{path}.content",
        )
        step_error = _optional_text(
            _field(raw_step, "error", path),
            f"{path}.error",
        )
        request_id = _optional_text(
            _field(raw_step, "approval_request_id", path),
            f"{path}.approval_request_id",
            non_empty=True,
        )
        decision = _optional_text(
            _field(raw_step, "approval_decision", path),
            f"{path}.approval_decision",
        )
        if decision is not None and decision not in _STEP_APPROVAL_DECISIONS:
            _invalid(f"{path}.approval_decision is unsupported")
        _validate_step_approval(
            path=path,
            requires_approval=definitions_by_name[name].requires_approval,
            status=step_status_text,
            request_id=request_id,
            decision=decision,
        )
        steps.append(
            StepSnapshot(
                name=name,
                status=step_status,
                content=content,
                error=step_error,
                attempts=attempts,
                approval_request_id=request_id,
                approval_decision=decision,
            )
        )

    if tuple(step.name for step in steps) != expected_names:
        _invalid("steps must match the bound Flow execution order")
    approvals = _parse_approval_records(raw_approvals, definitions_by_name)
    _validate_approval_projection(
        run_status=status,
        steps=steps,
        approvals=approvals,
        definitions_by_name=definitions_by_name,
    )
    return ValidatedCheckpointRecord(
        run_id=record_run_id,
        status=status,
        upstream_status=upstream_status,
        error=error,
        steps=tuple(steps),
    )


def _expected_step_names(definition: FlowDefinition) -> tuple[str, ...]:
    definition.validate()
    steps_by_name = {step.name: step for step in definition.steps}
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        for dependency in steps_by_name[name].depends_on:
            visit(dependency)
        visited.add(name)
        ordered.append(name)

    for step in definition.steps:
        visit(step.name)
    return tuple(ordered)


def _parse_approval_records(
    raw_approvals: Mapping[object, object],
    definitions_by_name: Mapping[str, FlowStepDefinition],
) -> dict[str, _ValidatedApprovalRecord]:
    approvals: dict[str, _ValidatedApprovalRecord] = {}
    for raw_step_name, raw_approval in raw_approvals.items():
        step_name = _required_text(raw_step_name, "approvals key")
        if step_name not in definitions_by_name:
            _invalid("approvals contains a step outside the bound Flow")
        if not definitions_by_name[step_name].requires_approval:
            _invalid("approvals contains a non-approval step")
        path = f"approvals.{step_name}"
        if not isinstance(raw_approval, Mapping):
            _invalid(f"{path} must be an object")
        action = _required_text(_field(raw_approval, "action", path), f"{path}.action")
        if action not in _STORED_APPROVAL_ACTIONS:
            _invalid(f"{path}.action is unsupported")
        request_id = _required_text(
            _field(raw_approval, "request_id", path),
            f"{path}.request_id",
        )
        _optional_text(_field(raw_approval, "reason", path), f"{path}.reason")
        _optional_object(
            _field(raw_approval, "arguments", path),
            f"{path}.arguments",
        )
        _optional_text(_field(raw_approval, "response", path), f"{path}.response")
        _optional_text(
            _field(raw_approval, "reviewer_id", path),
            f"{path}.reviewer_id",
        )
        _required_object(
            _field(raw_approval, "metadata", path),
            f"{path}.metadata",
        )
        decided_at = _required_text(
            _field(raw_approval, "decided_at", path),
            f"{path}.decided_at",
        )
        try:
            parsed_time = datetime.fromisoformat(decided_at)
        except (OverflowError, ValueError):
            _invalid(f"{path}.decided_at must be an ISO 8601 timestamp")
        if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
            _invalid(f"{path}.decided_at must include a timezone")
        approvals[step_name] = _ValidatedApprovalRecord(action, request_id)
    return approvals


def _validate_approval_projection(
    *,
    run_status: RunStatus,
    steps: list[StepSnapshot],
    approvals: Mapping[str, _ValidatedApprovalRecord],
    definitions_by_name: Mapping[str, FlowStepDefinition],
) -> None:
    for step in steps:
        if not definitions_by_name[step.name].requires_approval:
            continue
        approval = approvals.get(step.name)
        decision = step.approval_decision
        if decision in {"approve", "reject"}:
            if approval is None:
                _invalid(f"step {step.name} decision has no stored approval")
            if (
                approval.action != decision
                or approval.request_id != step.approval_request_id
            ):
                _invalid(f"step {step.name} approval fields do not agree")
        elif decision == "pending" and approval is not None:
            _invalid(f"step {step.name} pending decision has a stored approval")
        elif approval is not None and not (
            run_status is RunStatus.RUNNING and step.status is RunStatus.QUEUED
        ):
            _invalid(f"step {step.name} stored approval has no step projection")

        if step.status is RunStatus.WAITING_APPROVAL and decision is None:
            _invalid(f"step {step.name} waiting approval data is incomplete")
        if step.status is RunStatus.SUCCEEDED and decision != "approve":
            _invalid(f"step {step.name} succeeded without approval")
        if decision == "approve" and step.status not in {
            RunStatus.WAITING_APPROVAL,
            RunStatus.RUNNING,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
        }:
            _invalid(f"step {step.name} approved decision has an invalid status")
        if decision == "reject" and step.status not in {
            RunStatus.WAITING_APPROVAL,
            RunStatus.CANCELED,
        }:
            _invalid(f"step {step.name} rejected decision has an invalid status")


def _validate_step_approval(
    *,
    path: str,
    requires_approval: bool,
    status: str,
    request_id: str | None,
    decision: str | None,
) -> None:
    if not requires_approval and (request_id is not None or decision is not None):
        _invalid(f"{path} has approval data for a non-approval step")
    if (request_id is None) != (decision is None):
        _invalid(f"{path} approval request and decision must appear together")
    if decision == "pending" and status != "waiting_approval":
        _invalid(f"{path} has a pending decision outside waiting_approval")


def _field(value: Mapping[object, object], key: str, path: str) -> object:
    if key not in value:
        _invalid(f"{path}.{key} is required")
    return value[key]


def _required_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        _invalid(f"{path} must be a string")
    return value


def _required_text(value: object, path: str) -> str:
    text = _required_string(value, path)
    if not text.strip():
        _invalid(f"{path} must not be empty")
    return text


def _optional_text(value: object, path: str, *, non_empty: bool = False) -> str | None:
    if value is None:
        return None
    text = _required_string(value, path)
    if non_empty and not text.strip():
        _invalid(f"{path} must not be empty")
    return text


def _required_object(value: object, path: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        _invalid(f"{path} must be an object with string keys")
    return value


def _optional_object(value: object, path: str) -> Mapping[object, object] | None:
    if value is None:
        return None
    return _required_object(value, path)


def _non_negative_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _invalid(f"{path} must be a non-negative integer")
    return value


def _finite_number(value: object, path: str, *, non_negative: bool = False) -> int | float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or (isinstance(value, float) and not math.isfinite(value))
        or (non_negative and value < 0)
    ):
        qualifier = "non-negative finite " if non_negative else "finite "
        _invalid(f"{path} must be a {qualifier}number")
    return value


def _optional_finite_number(
    value: object,
    path: str,
    *,
    non_negative: bool = False,
) -> int | float | None:
    if value is None:
        return None
    return _finite_number(value, path, non_negative=non_negative)


def _parse_status(value: object, path: str) -> tuple[str, RunStatus]:
    text = _required_text(value, path)
    status = _STATUS_MAP.get(text)
    if status is None:
        _invalid(f"{path} is unsupported")
    return text, status


def _invalid(reason: str) -> NoReturn:
    raise PactTrailError(f"invalid_checkpoint: {reason}")
