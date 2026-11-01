from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any


class PactTrailError(RuntimeError):
    """Expected product error safe to show at the CLI boundary."""


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True)
class StepSnapshot:
    name: str
    status: RunStatus
    content: str = ""
    error: str | None = None
    attempts: int = 0
    approval_request_id: str | None = None
    approval_decision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "content": self.content,
            "error": self.error,
            "attempts": self.attempts,
            "approval_request_id": self.approval_request_id,
            "approval_decision": self.approval_decision,
        }


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_id: str
    flow_id: str
    flow_version: str
    status: RunStatus
    content: str
    error: str | None
    steps: tuple[StepSnapshot, ...]
    upstream_status: str
    flow_digest: str = ""

    @property
    def waiting_step(self) -> StepSnapshot | None:
        return next(
            (step for step in self.steps if step.status is RunStatus.WAITING_APPROVAL),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "flow_id": self.flow_id,
            "flow_version": self.flow_version,
            "flow_digest": self.flow_digest,
            "status": self.status.value,
            "content": self.content,
            "error": self.error,
            "waiting_step": self.waiting_step.name if self.waiting_step else None,
            "upstream_status": self.upstream_status,
            "steps": [step.to_dict() for step in self.steps],
        }


_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def validate_run_id(run_id: str) -> str:
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise PactTrailError(
            "run_id must contain 1-128 ASCII letters, digits, underscores, or hyphens"
        )
    return run_id
