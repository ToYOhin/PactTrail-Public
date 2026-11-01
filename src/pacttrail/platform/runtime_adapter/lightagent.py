from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

from pacttrail import __version__
from pacttrail.domain.run_bindings import RunBinding
from pacttrail.domain.runs import (
    PactTrailError,
    RunSnapshot,
    RunStatus,
    validate_run_id,
)
from pacttrail.domain.workflows import FlowDefinition
from pacttrail.platform.run_binding_store import FileRunBindingStore
from pacttrail.platform.runtime_adapter.checkpoint_records import (
    ValidatedCheckpointRecord,
    parse_checkpoint_record,
)


EXPECTED_UPSTREAM_VERSION = "0.10.0"

@dataclass(frozen=True, slots=True)
class _RuntimeBindings:
    flow_type: type
    store_type: type


@lru_cache(maxsize=1)
def _load_runtime() -> _RuntimeBindings:
    """Load upstream symbols lazily so `doctor` can report a broken install."""
    try:
        # This is the only product module allowed to import the upstream package.
        from LightAgent import JsonLightFlowStore, LightFlow
    except Exception as exc:
        raise PactTrailError(
            f"LightAgent runtime import failed: {type(exc).__name__}: {exc}"
        ) from exc
    return _RuntimeBindings(flow_type=LightFlow, store_type=JsonLightFlowStore)


class LightAgentRuntimeAdapter:
    """Translate LightAgent's public LightFlow API into PactTrail contracts."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self.runs_dir = self.data_dir / "runs"
        self.binding_store = FileRunBindingStore(self.data_dir)

    def doctor(self) -> dict[str, Any]:
        try:
            installed = version("LightAgent")
        except PackageNotFoundError:
            installed = None
        runtime_error = None
        if installed is None:
            runtime_error = "LightAgent distribution is not installed"
        else:
            try:
                _load_runtime()
            except PactTrailError as exc:
                runtime_error = str(exc)
        return {
            "product": "PactTrail",
            "product_version": __version__,
            "runtime": "LightAgent",
            "expected_runtime_version": EXPECTED_UPSTREAM_VERSION,
            "installed_runtime_version": installed,
            "ready": installed == EXPECTED_UPSTREAM_VERSION and runtime_error is None,
            "runtime_error": runtime_error,
            "mode": "offline-scripted",
            "live_provider_enabled": False,
        }

    def start_flow(
        self,
        definition: FlowDefinition,
        task: str,
        *,
        run_id: str | None = None,
    ) -> RunSnapshot:
        normalized_task = task.strip()
        if not normalized_task:
            raise PactTrailError("task must not be empty")
        if len(normalized_task) > 4000:
            raise PactTrailError("task must not exceed 4000 characters")

        selected_run_id = validate_run_id(run_id or uuid4().hex)
        binding = RunBinding.for_flow(selected_run_id, definition)
        if self.binding_store.has_claim(selected_run_id):
            raise PactTrailError(
                f"run already exists or is reserved: {selected_run_id}"
            )
        if self._checkpoint_exists(selected_run_id):
            raise PactTrailError(
                "invalid_run_binding: existing checkpoint is not bound; "
                f"recreate the run with a new run_id: {selected_run_id}"
            )
        flow = self._build_flow(definition)
        if self._get_record(flow, selected_run_id) is not None:
            raise PactTrailError(
                "invalid_run_binding: existing upstream run is not bound; "
                f"recreate it with a new run_id: {selected_run_id}"
            )

        self.binding_store.create(binding)
        if self._get_record(flow, selected_run_id) is not None:
            raise PactTrailError(
                f"run already exists after binding reservation: {selected_run_id}"
        )
        flow.run(normalized_task, trace=True, run_id=selected_run_id)
        record = self._load_checkpoint(flow, selected_run_id, definition)
        return self._snapshot(binding, record)

    def record_decision(
        self,
        definition: FlowDefinition,
        run_id: str,
        *,
        step_name: str,
        action: str,
    ) -> None:
        selected_run_id = validate_run_id(run_id)
        if action not in {"approve", "reject"}:
            raise PactTrailError("decision must be approve or reject")

        self._load_and_verify_binding(definition, selected_run_id)
        flow = self._build_flow(definition)
        self._load_checkpoint(flow, selected_run_id, definition)
        decision: bool | dict[str, str]
        if action == "approve":
            decision = True
        else:
            decision = {
                "action": "reject",
                "reason": "rejected by PactTrail operator",
            }
        try:
            flow.approve(selected_run_id, step_name, decision)
        except ValueError as exc:
            raise PactTrailError(str(exc)) from exc

    def resume_flow(
        self,
        definition: FlowDefinition,
        run_id: str,
    ) -> RunSnapshot:
        selected_run_id = validate_run_id(run_id)
        binding = self._load_and_verify_binding(definition, selected_run_id)
        flow = self._build_flow(definition)
        self._load_checkpoint(flow, selected_run_id, definition)
        try:
            flow.resume(selected_run_id, trace=True)
        except ValueError as exc:
            raise PactTrailError(str(exc)) from exc
        record = self._load_checkpoint(flow, selected_run_id, definition)
        return self._snapshot(binding, record)

    def get_run(self, definition: FlowDefinition, run_id: str) -> RunSnapshot:
        selected_run_id = validate_run_id(run_id)
        binding = self._load_and_verify_binding(definition, selected_run_id)
        flow = self._build_flow(definition)
        record = self._load_checkpoint(flow, selected_run_id, definition)
        return self._snapshot(binding, record)

    def _load_and_verify_binding(
        self,
        definition: FlowDefinition,
        run_id: str,
    ) -> RunBinding:
        binding = self.binding_store.load(run_id)
        if binding is None:
            if self._checkpoint_exists(run_id):
                raise PactTrailError(
                    "invalid_run_binding: existing checkpoint has no exact Flow "
                    f"binding; recreate the run with a new run_id: {run_id}"
                )
            raise PactTrailError(f"run not found: {run_id}")
        binding.verify_definition(definition)
        if not self._checkpoint_exists(run_id):
            raise PactTrailError(
                "invalid_run_binding: binding exists but checkpoint is missing; "
                f"run_id remains reserved: {run_id}"
            )
        return binding

    def _checkpoint_exists(self, run_id: str) -> bool:
        return (self.runs_dir / f"{validate_run_id(run_id)}.json").exists()

    def _build_flow(self, definition: FlowDefinition) -> Any:
        definition.validate()
        runtime = _load_runtime()
        store = runtime.store_type(self.runs_dir)
        flow = runtime.flow_type(store=store)
        for step in definition.steps:
            flow.step(
                step.name,
                agent=step.agent,
                depends_on=list(step.depends_on),
                query=step.query,
                tools=list(step.tools),
                max_retry=step.max_attempts,
                timeout=step.timeout_seconds,
                requires_approval=step.requires_approval,
            )
        return flow

    @staticmethod
    def _get_record(flow: Any, run_id: str) -> object | None:
        try:
            return flow.get_run(run_id)
        except (OSError, TypeError, UnicodeError, ValueError, RecursionError) as exc:
            raise PactTrailError(
                f"invalid_checkpoint: checkpoint could not be read for run {run_id}"
            ) from exc

    def _load_checkpoint(
        self,
        flow: Any,
        run_id: str,
        definition: FlowDefinition,
    ) -> ValidatedCheckpointRecord:
        record = self._get_record(flow, run_id)
        if record is None:
            if self._checkpoint_exists(run_id):
                raise PactTrailError(
                    "invalid_checkpoint: record must be an object"
                )
            raise PactTrailError(f"run not found: {run_id}")
        return parse_checkpoint_record(
            record,
            requested_run_id=run_id,
            definition=definition,
        )

    @staticmethod
    def _snapshot(
        binding: RunBinding,
        record: ValidatedCheckpointRecord,
    ) -> RunSnapshot:
        steps = record.steps
        if record.status in {RunStatus.FAILED, RunStatus.CANCELED}:
            steps = tuple(
                replace(
                    step,
                    status=RunStatus.CANCELED,
                    error=step.error or "not executed because the run is terminal",
                )
                if step.status is RunStatus.QUEUED
                else step
                for step in steps
            )
        content = next(
            (step.content for step in reversed(steps) if step.content),
            "",
        )
        return RunSnapshot(
            run_id=record.run_id,
            flow_id=binding.flow_id,
            flow_version=binding.flow_version,
            flow_digest=binding.flow_digest,
            status=record.status,
            content=content,
            error=record.error,
            steps=steps,
            upstream_status=record.upstream_status,
        )
