from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from pacttrail.domain.ports import RuntimeEngine
from pacttrail.domain.runs import RunSnapshot, validate_run_id
from pacttrail.application.demo_workflow import (
    DEMO_APPROVAL_STEP,
    build_demo_workflow,
)


@dataclass(frozen=True, slots=True)
class DemoResult:
    run: RunSnapshot
    artifact_path: Path

    def to_dict(self) -> dict[str, object]:
        payload = self.run.to_dict()
        payload["artifact_path"] = str(self.artifact_path)
        return payload


class DemoService:
    def __init__(self, runtime: RuntimeEngine, data_dir: str | Path):
        self.runtime = runtime
        self.data_dir = Path(data_dir).resolve()
        self.definition = build_demo_workflow()

    def start(self, task: str, *, run_id: str | None = None) -> DemoResult:
        return self._persist(
            self.runtime.start_flow(self.definition, task, run_id=run_id)
        )

    def decide(self, run_id: str, *, action: str) -> DemoResult:
        self.record_decision(run_id, action=action)
        return self.resume(run_id)

    def record_decision(self, run_id: str, *, action: str) -> None:
        self.runtime.record_decision(
            self.definition,
            run_id,
            step_name=DEMO_APPROVAL_STEP,
            action=action,
        )

    def resume(self, run_id: str) -> DemoResult:
        return self._persist(self.runtime.resume_flow(self.definition, run_id))

    def status(self, run_id: str) -> RunSnapshot:
        return self.runtime.get_run(self.definition, run_id)

    def _persist(self, snapshot: RunSnapshot) -> DemoResult:
        safe_run_id = validate_run_id(snapshot.run_id)
        artifacts_dir = self.data_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifacts_dir / f"{safe_run_id}.json"
        temporary_path = artifacts_dir / f".{safe_run_id}.tmp"
        payload = {
            "schema_version": "pacttrail.run.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "product": "PactTrail",
            "run": snapshot.to_dict(),
        }
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(artifact_path)
        return DemoResult(run=snapshot, artifact_path=artifact_path)
