from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol

from .definitions import canonical_json
from .evidence import MAX_OUTPUT_BYTES, SAMPLE_FILES
from .policy_definitions import PolicyDefinition, PolicyRule
from .runs import PactTrailError
from .tool_manifests import ToolManifest


REPORT_SCOPE = 'pacttrail.evidence.report'
REPORT_TARGET = 'report.json'
RECORD_LIMIT = 131072
TTL_SECONDS = 900
SOURCE_HASHES = (
    'fe037c0604f90a27688528ad232ad83f390e5dce650167d7a2672a0ac805a14f',
    '7dfb2899452caa68255a7163995c1b95090957d56d951585fe7b42ea2628ea75',
    '6d120c84c4b0b1a18ad270273e162994d4adffa34aa25930eb603357b963a8d6',
)
_CODES = frozenset(('invalid_request', 'access_denied', 'approval_required', 'denied',
                    'expired', 'source_changed', 'state_unavailable', 'unresolved',
                    'output_unavailable'))


def report_error(code: str) -> PactTrailError:
    return PactTrailError('evidence_report_error: ' + code)


def safe_report_error(error: Exception, fallback='state_unavailable') -> PactTrailError:
    if (type(error) is PactTrailError and len(error.args) == 1 and type(error.args[0]) is str
            and error.args[0] in {'evidence_report_error: ' + code for code in _CODES}):
        return error
    return report_error(fallback)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: object) -> str:
    return sha256(canonical_json(value).encode('utf-8'))


def token(value: object, pattern: str) -> str:
    if type(value) is not str or re.fullmatch(pattern, value) is None:
        raise report_error('invalid_request')
    return value


def timestamp(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 4102444800:
        raise report_error('invalid_request')
    return value


def file_identity(value: object) -> tuple[int, int]:
    if (type(value) not in (list, tuple) or len(value) != 2
            or any(type(item) is not int or not 0 < item < 2**128 for item in value)):
        raise report_error('unresolved')
    return tuple(value)


def parse_json(text: str) -> dict:
    def nonfinite(_):
        raise report_error('unresolved')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise report_error('unresolved')
            result[key] = value
        return result
    if type(text) is not str or len(text.encode('utf-8')) > RECORD_LIMIT:
        raise report_error('unresolved')
    value = json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)
    if type(value) is not dict:
        raise report_error('unresolved')
    return value


def report_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id='pacttrail.evidence.write-report', version='1.0.0',
        description='Publish one explicitly approved fixed synthetic report.',
        input_schema={'type': 'object', 'required': ['proposal_sha256'],
                      'additionalProperties': False,
                      'properties': {'proposal_sha256': {'type': 'string', 'maxLength': 64}}},
        output_schema={'type': 'object'}, implementation_kind='pacttrail-adapter',
        risk_level='L1', side_effect='recoverable', allowed_scopes=(REPORT_SCOPE,),
        supports_dry_run=True, supports_idempotency=True, timeout_seconds=1,
        max_output_bytes=RECORD_LIMIT, secret_fields=(), retention_policy_ref=None,
    )


def report_policy() -> PolicyDefinition:
    return PolicyDefinition(
        policy_id='pacttrail.evidence.report-policy', version='1.0.0',
        description='Only an exact explicitly approved synthetic report may be published.',
        priority=0, default_result='deny', rules=(PolicyRule(
            rule_id='pacttrail.evidence.approve-report', priority=0,
            result='require_approval', reason_code='pacttrail.evidence.explicit-approval',
            subjects=(REPORT_SCOPE,), tool_refs=(report_manifest().definition_ref,),
            risk_levels=('L1',), side_effects=('recoverable',),
            required_scopes=(REPORT_SCOPE,), data_labels=('synthetic',),
        ),),
    )


@dataclass(frozen=True, slots=True)
class ReportProposal:
    run_id: str
    operator: str
    root: str
    root_identity: tuple[int, int]
    query: str
    report_utf8: str
    created_at: int

    def __post_init__(self):
        token(self.run_id, r'[0-9a-f]{32}')
        token(self.operator, r'[a-z][a-z0-9-]{0,31}')
        if (type(self.root) is not str or not 1 <= len(self.root) <= 1024
                or any(ord(c) < 32 or ord(c) == 127 for c in self.root)):
            raise report_error('invalid_request')
        self.root.encode('utf-8')
        if type(self.root_identity) is not tuple:
            raise report_error('unresolved')
        file_identity(self.root_identity)
        timestamp(self.created_at)
        timestamp(self.created_at + TTL_SECONDS)
        if (type(self.query) is not str or not 1 <= len(self.query) <= 128
                or self.query != self.query.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in self.query)):
            raise report_error('invalid_request')
        if (type(self.report_utf8) is not str
                or not 1 <= len(self.report_utf8.encode('utf-8')) <= MAX_OUTPUT_BYTES
                or not self.report_utf8.endswith('\n')):
            raise report_error('invalid_request')
        report = parse_json(self.report_utf8)
        if (report.get('schema_version') != 'pacttrail.evidence-preview.v1'
                or report.get('query') != self.query or report.get('files_checked') != list(SAMPLE_FILES)):
            raise report_error('unresolved')

    def to_dict(self) -> dict:
        return {
            'schema_version': 'pacttrail.evidence-report-proposal.v1',
            'run_id': self.run_id, 'step_id': 'publish-report',
            'tool_ref': report_manifest().definition_ref.to_dict(),
            'policy_ref': report_policy().definition_ref.to_dict(),
            'scope': REPORT_SCOPE, 'operator': self.operator, 'root': self.root,
            'root_identity': list(self.root_identity), 'target': REPORT_TARGET,
            'arguments': {'query': self.query, 'sample': True},
            'arguments_sha256': digest({'query': self.query, 'sample': True}),
            'sources': dict(zip(SAMPLE_FILES, SOURCE_HASHES)),
            'sources_sha256': digest(dict(zip(SAMPLE_FILES, SOURCE_HASHES))),
            'report_utf8': self.report_utf8,
            'report_sha256': sha256(self.report_utf8.encode('utf-8')),
            'report_bytes': len(self.report_utf8.encode('utf-8')),
            'created_at': self.created_at, 'expires_at': self.created_at + TTL_SECONDS,
        }

    @classmethod
    def from_dict(cls, value: dict) -> ReportProposal:
        if type(value) is not dict:
            raise report_error('unresolved')
        result = cls(value['run_id'], value['operator'], value['root'],
                     file_identity(value['root_identity']), value['arguments']['query'],
                     value['report_utf8'], value['created_at'])
        # Canonical comparison preserves JSON boolean/number distinctions.
        if canonical_json(value) != canonical_json(result.to_dict()):
            raise report_error('unresolved')
        return result


class ReportStore(Protocol):
    def review(self, query: str, operator: str, report: str, now: int) -> dict: ...
    def load(self, run_id: str, operator: str, proposal_sha256: str) -> dict: ...
    def decide(self, run_id: str, operator: str, proposal_sha256: str, decision: str, now: int) -> dict: ...
    def publish(self, run_id: str, operator: str, proposal_sha256: str,
                now: int, sources: tuple[str, ...] | None) -> dict: ...


@dataclass(frozen=True, slots=True)
class ReportBinding:
    manifest: ToolManifest
    store: ReportStore


def authorize_report(binding: ReportBinding, policy: PolicyDefinition) -> None:
    try:
        if (type(binding) is ReportBinding and type(binding.manifest) is ToolManifest
                and type(policy) is PolicyDefinition
                and ToolManifest.from_dict(binding.manifest.to_dict()) == report_manifest()
                and PolicyDefinition.from_dict(policy.to_dict()) == report_policy()):
            return
    except Exception:
        pass
    raise report_error('access_denied')
