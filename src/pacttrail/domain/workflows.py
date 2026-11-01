from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Protocol

from .definitions import (
    DefinitionRef,
    canonical_json as encode_canonical_json,
    canonical_sha256,
    validate_definition_id,
    validate_semantic_version,
)
from .runs import PactTrailError


class AgentRunner(Protocol):
    name: str
    definition_ref: DefinitionRef

    def run(self, query: str, **kwargs: Any) -> Any: ...


class QueryBuilder(Protocol):
    definition_ref: DefinitionRef

    def __call__(self, original_query: str, context: dict[str, Any]) -> str: ...


@dataclass(frozen=True, slots=True)
class FlowStepDefinition:
    name: str
    agent: AgentRunner
    depends_on: tuple[str, ...] = ()
    query: str | QueryBuilder | None = None
    tools: tuple[Any, ...] = ()
    max_attempts: int = 1
    timeout_seconds: float | None = None
    requires_approval: bool = False


@dataclass(frozen=True, slots=True)
class FlowDefinition:
    flow_id: str
    version: str
    steps: tuple[FlowStepDefinition, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if (
            not isinstance(self.flow_id, str)
            or not self.flow_id.strip()
            or not isinstance(self.version, str)
            or not self.version.strip()
        ):
            raise PactTrailError("flow_id and version must not be empty")
        if not self.steps:
            raise PactTrailError("flow must contain at least one step")

        names = [step.name for step in self.steps]
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise PactTrailError("flow step names must not be empty")
        if len(names) != len(set(names)):
            raise PactTrailError("flow step names must be unique")

        known = set(names)
        for step in self.steps:
            if not callable(getattr(step.agent, "run", None)):
                raise PactTrailError(f"agent for step {step.name} must provide run()")
            if not isinstance(getattr(step.agent, "name", None), str) or not step.agent.name:
                raise PactTrailError(f"agent for step {step.name} must provide a name")
            if step.query is not None and not isinstance(step.query, str):
                if not callable(step.query):
                    raise PactTrailError(
                        f"query for step {step.name} must be text or a callable"
                    )
            if (
                not isinstance(step.max_attempts, int)
                or isinstance(step.max_attempts, bool)
                or step.max_attempts < 1
            ):
                raise PactTrailError(f"step {step.name} max_attempts must be at least 1")
            if step.timeout_seconds is not None:
                if (
                    not isinstance(step.timeout_seconds, (int, float))
                    or isinstance(step.timeout_seconds, bool)
                    or not math.isfinite(float(step.timeout_seconds))
                    or step.timeout_seconds <= 0
                ):
                    raise PactTrailError(
                        f"step {step.name} timeout_seconds must be positive and finite"
                    )
            if not isinstance(step.requires_approval, bool):
                raise PactTrailError(
                    f"step {step.name} requires_approval must be a boolean"
                )
            if any(
                not isinstance(dependency, str) or not dependency
                for dependency in step.depends_on
            ):
                raise PactTrailError(
                    f"step {step.name} dependencies must be non-empty strings"
                )
            unknown = set(step.depends_on) - known
            if unknown:
                raise PactTrailError(
                    f"step {step.name} has unknown dependencies: {sorted(unknown)}"
                )

        dependencies = {step.name: step.depends_on for step in self.steps}
        temporary: set[str] = set()
        complete: set[str] = set()

        def visit(name: str) -> None:
            if name in complete:
                return
            if name in temporary:
                raise PactTrailError(f"flow dependency cycle detected at step {name}")
            temporary.add(name)
            for dependency in dependencies[name]:
                visit(dependency)
            temporary.remove(name)
            complete.add(name)

        for name in names:
            visit(name)

    def validate_identity(self) -> None:
        validate_definition_id(self.flow_id)
        validate_semantic_version(self.version)
        for step in self.steps:
            _definition_ref(step.agent, f"agent for step {step.name}")
            if step.query is not None and not isinstance(step.query, str):
                _definition_ref(step.query, f"query for step {step.name}")
            for index, tool in enumerate(step.tools):
                _definition_ref(tool, f"tool {index} for step {step.name}")

    def canonical_payload(self) -> dict[str, Any]:
        self.validate()
        self.validate_identity()
        steps: list[dict[str, Any]] = []
        for step in self.steps:
            if step.query is None:
                query: dict[str, Any] = {"kind": "default"}
            elif isinstance(step.query, str):
                query = {"kind": "literal", "value": step.query}
            else:
                query = {
                    "kind": "definition_ref",
                    "ref": _definition_ref(
                        step.query,
                        f"query for step {step.name}",
                    ).to_dict(),
                }
            steps.append(
                {
                    "name": step.name,
                    "agent_ref": _definition_ref(
                        step.agent,
                        f"agent for step {step.name}",
                    ).to_dict(),
                    "depends_on": list(step.depends_on),
                    "query": query,
                    "tool_refs": [
                        _definition_ref(
                            tool,
                            f"tool {index} for step {step.name}",
                        ).to_dict()
                        for index, tool in enumerate(step.tools)
                    ],
                    "max_attempts": step.max_attempts,
                    "timeout_seconds": (
                        float(step.timeout_seconds)
                        if step.timeout_seconds is not None
                        else None
                    ),
                    "requires_approval": step.requires_approval,
                }
            )
        return {
            "schema_version": "pacttrail.flow.v1",
            "flow_id": self.flow_id,
            "version": self.version,
            "steps": steps,
        }

    def canonical_json(self) -> str:
        return encode_canonical_json(self.canonical_payload())

    @property
    def digest(self) -> str:
        return canonical_sha256(self.canonical_payload())

    @property
    def definition_ref(self) -> DefinitionRef:
        return DefinitionRef(self.flow_id, self.version, self.digest)


def _definition_ref(value: object, label: str) -> DefinitionRef:
    try:
        reference = getattr(value, "definition_ref")
    except Exception as exc:
        raise PactTrailError(
            f"{label} must provide an exact definition ref"
        ) from exc
    if not isinstance(reference, DefinitionRef):
        raise PactTrailError(f"{label} must provide an exact definition ref")
    return reference
