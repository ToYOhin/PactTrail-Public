from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pacttrail.platform.providers.scripted as _scripted_provider
from pacttrail.domain.definitions import (
    AgentDefinition,
    DefinitionRef,
    DeterministicTemplateExecution,
    legacy_scripted_definition_ref,
)
from pacttrail.domain.runs import PactTrailError
from pacttrail.domain.workflows import FlowDefinition, FlowStepDefinition
from pacttrail.platform.providers import (
    ScriptedModelProvider,
    ScriptedModelRequest,
)


DEMO_APPROVAL_STEP = "execute"

_REQUEST_COMPATIBILITY_REJECTIONS = frozenset(
    {
        "scripted_model_provider_error: invalid request",
        "scripted_model_provider_error: input exceeds limits",
    }
)
_PREFLIGHT_COMPATIBILITY_REJECTIONS = frozenset(
    {
        "scripted_model_provider_error: input exceeds limits",
        "scripted_model_provider_error: unsupported template",
        "scripted_model_provider_error: output exceeds limits",
    }
)


def _is_compatibility_rejection(
    error: PactTrailError,
    allowed_messages: frozenset[str],
) -> bool:
    return (
        type(error) is PactTrailError
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in allowed_messages
    )


def _legacy_render_once(response_template: str, query: str) -> str:
    return response_template.format(query=query.strip())


def _render_scripted_response(response_template: str, query: str) -> str:
    request: ScriptedModelRequest | None
    try:
        request = ScriptedModelRequest(response_template, query)
    except PactTrailError as error:
        if _is_compatibility_rejection(
            error,
            _REQUEST_COMPATIBILITY_REJECTIONS,
        ):
            request = None
        else:
            raise

    # Leave the provider exception handler before invoking legacy code so a
    # legacy failure keeps its original exception context as well as its type.
    if request is None:
        return _legacy_render_once(response_template, query)

    stripped_query = request.query.strip()
    preflight_rejected = False
    try:
        _scripted_provider._preflight_template(
            request.response_template,
            stripped_query,
        )
    except PactTrailError as error:
        if _is_compatibility_rejection(
            error,
            _PREFLIGHT_COMPATIBILITY_REJECTIONS,
        ):
            preflight_rejected = True
        else:
            raise

    # Preflight never renders.  Once public render starts, no error may reach
    # this fallback because that could execute a hostile template twice.
    if preflight_rejected:
        return _legacy_render_once(response_template, query)

    return ScriptedModelProvider().render(request).text


@dataclass(frozen=True, slots=True)
class ScriptedAgent:
    """Deterministic offline Agent used by the product demo and tests."""

    name: str
    response_template: str
    version: str = "1.0.0"

    @property
    def agent_definition(self) -> AgentDefinition:
        return AgentDefinition(
            agent_id=self.name,
            version=self.version,
            role="offline scripted agent",
            objective=(
                "Render one deterministic offline response from the supplied query."
            ),
            execution=DeterministicTemplateExecution(
                response_template=self.response_template,
                strip_query=True,
            ),
        )

    @property
    def definition_ref(self) -> DefinitionRef:
        # Removing this projection requires a CR3 Run-binding migration.
        return legacy_scripted_definition_ref(self.agent_definition)

    def run(self, query: str, **_: Any) -> str:
        return _render_scripted_response(self.response_template, query)


@dataclass(frozen=True, slots=True)
class ExactTaskQuery:
    definition_id: str = "pacttrail.query.exact-task"
    version: str = "1.0.0"

    @property
    def definition_ref(self) -> DefinitionRef:
        payload = {
            "schema_version": "pacttrail.query-builder.v1",
            "query_id": self.definition_id,
            "version": self.version,
            "operation": "return-original-query",
        }
        return DefinitionRef.from_payload(self.definition_id, self.version, payload)

    def __call__(self, original_query: str, _: dict[str, Any]) -> str:
        return original_query


_EXACT_TASK = ExactTaskQuery()


def build_demo_workflow() -> FlowDefinition:
    intake_agent = ScriptedAgent("intake-agent", "任务接收完成：{query}")
    planner_agent = ScriptedAgent(
        "planner-agent",
        "离线计划：分析任务《{query}》；"
        "执行前等待人工批准；执行后由 Reviewer 复核。",
    )
    executor_agent = ScriptedAgent(
        "executor-agent",
        "离线执行完成：已处理《{query}》；"
        "未访问网络、Shell、外部文件或真实模型。",
    )
    reviewer_agent = ScriptedAgent(
        "reviewer-agent",
        "复核通过：任务《{query}》已完成离线流程；"
        "该结果仅证明编排、审批、恢复和产物链路可用。",
    )
    return FlowDefinition(
        flow_id="pacttrail.offline-approval-demo",
        version="1.0.0",
        steps=(
            FlowStepDefinition(
                name="intake",
                agent=intake_agent,
                query=_EXACT_TASK,
            ),
            FlowStepDefinition(
                name="plan",
                agent=planner_agent,
                depends_on=("intake",),
                query=_EXACT_TASK,
            ),
            FlowStepDefinition(
                name=DEMO_APPROVAL_STEP,
                agent=executor_agent,
                depends_on=("plan",),
                query=_EXACT_TASK,
                requires_approval=True,
            ),
            FlowStepDefinition(
                name="review",
                agent=reviewer_agent,
                depends_on=(DEMO_APPROVAL_STEP,),
                query=_EXACT_TASK,
            ),
        ),
    )
