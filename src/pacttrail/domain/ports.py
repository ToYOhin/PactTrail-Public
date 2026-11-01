from __future__ import annotations

from typing import Any, Protocol

from .runs import RunSnapshot
from .workflows import FlowDefinition


class RuntimeEngine(Protocol):
    """Product-facing runtime port implemented by an upstream adapter."""

    def doctor(self) -> dict[str, Any]: ...

    def start_flow(
        self,
        definition: FlowDefinition,
        task: str,
        *,
        run_id: str | None = None,
    ) -> RunSnapshot: ...

    def record_decision(
        self,
        definition: FlowDefinition,
        run_id: str,
        *,
        step_name: str,
        action: str,
    ) -> None: ...

    def resume_flow(
        self,
        definition: FlowDefinition,
        run_id: str,
    ) -> RunSnapshot: ...

    def get_run(self, definition: FlowDefinition, run_id: str) -> RunSnapshot: ...
