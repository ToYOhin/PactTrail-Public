from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .definitions import DefinitionRef
from .policy_definitions import PolicyDefinition, PolicyRule
from .runs import PactTrailError
from .tool_manifests import ToolManifest


SAMPLE_FILES = ("overview.txt", "controls.txt", "recovery.txt")
SAMPLE_SCOPE = "pacttrail.evidence.sample"
SAMPLE_SUBJECT = "pacttrail.evidence.preview"
MAX_QUERY_CHARACTERS = 128
MAX_FILE_BYTES = 4096
MAX_TOTAL_BYTES = 12288
MAX_MATCHES = 20
MAX_OUTPUT_BYTES = 32768
_ERROR_PREFIX = "evidence_preview_error: "
_SAFE_ERRORS = frozenset(
    _ERROR_PREFIX + code for code in (
        "invalid_request", "access_denied", "source_unavailable", "invalid_source",
        "input_limit", "match_limit", "output_limit", "output_unavailable",
    )
)


def evidence_error(code: str) -> PactTrailError:
    return PactTrailError(_ERROR_PREFIX + code)


def safe_evidence_error(error: Exception, fallback: str) -> PactTrailError:
    if (type(error) is PactTrailError and len(error.args) == 1
            and type(error.args[0]) is str and error.args[0] in _SAFE_ERRORS):
        return error
    return evidence_error(fallback)


class SampleReader(Protocol):
    def read(self, file_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class SampleReadRequest:
    file_id: str
    tool_ref: DefinitionRef
    subject: str = SAMPLE_SUBJECT
    scope: str = SAMPLE_SCOPE


@dataclass(frozen=True, slots=True)
class SampleReadBinding:
    manifest: ToolManifest
    reader: SampleReader


def sample_read_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="pacttrail.evidence.read-sample",
        version="1.0.0",
        description="Read one fixed synthetic evidence text without writes.",
        input_schema={"type": "object", "required": ["file_id"],
                      "additionalProperties": False,
                      "properties": {"file_id": {"enum": list(SAMPLE_FILES)}}},
        output_schema={"type": "string", "maxLength": MAX_FILE_BYTES},
        implementation_kind="pacttrail-adapter",
        risk_level="L1",
        side_effect="none",
        allowed_scopes=(SAMPLE_SCOPE,),
        supports_dry_run=False,
        supports_idempotency=False,
        timeout_seconds=1,
        max_output_bytes=MAX_FILE_BYTES,
        secret_fields=(),
        retention_policy_ref=None,
    )


def sample_read_policy() -> PolicyDefinition:
    return PolicyDefinition(
        policy_id="pacttrail.evidence.sample-policy",
        version="1.0.0",
        description="Only the fixed synthetic evidence read profile is supported.",
        priority=0,
        default_result="deny",
        rules=(PolicyRule(
            rule_id="pacttrail.evidence.allow-sample",
            priority=0,
            result="allow",
            reason_code="pacttrail.evidence.fixed-sample",
            subjects=(SAMPLE_SUBJECT,),
            tool_refs=(sample_read_manifest().definition_ref,),
            risk_levels=("L1",),
            side_effects=("none",),
            required_scopes=(SAMPLE_SCOPE,),
            data_labels=("synthetic",),
        ),),
    )


def authorize_sample_read(
    request: SampleReadRequest, binding: SampleReadBinding, policy: PolicyDefinition,
) -> None:
    # Exact roles and the complete fixed profiles matter; a self-reported digest
    # or an arbitrary well-formed allow policy is not execution authority.
    try:
        if (type(request) is SampleReadRequest
                and type(binding) is SampleReadBinding
                and type(binding.manifest) is ToolManifest
                and type(policy) is PolicyDefinition
                and type(request.file_id) is str and request.file_id in SAMPLE_FILES
                and type(request.subject) is str and request.subject == SAMPLE_SUBJECT
                and type(request.scope) is str and request.scope == SAMPLE_SCOPE
                and type(request.tool_ref) is DefinitionRef
                and all(type(value) is str for value in (
                    request.tool_ref.definition_id, request.tool_ref.version, request.tool_ref.digest
                ))
                and request.tool_ref == sample_read_manifest().definition_ref
                and ToolManifest.from_dict(binding.manifest.to_dict()) == sample_read_manifest()
                and PolicyDefinition.from_dict(policy.to_dict()) == sample_read_policy()):
            return
    except Exception:
        pass
    raise evidence_error("access_denied") from None
