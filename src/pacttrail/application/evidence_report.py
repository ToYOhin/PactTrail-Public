from __future__ import annotations

import time

from pacttrail.application.evidence_preview import preview_evidence
from pacttrail.domain.evidence import SampleReadBinding
from pacttrail.domain.evidence_report import (
    SOURCE_HASHES, ReportBinding, authorize_report, report_error, safe_report_error, sha256, token,
)
from pacttrail.domain.policy_definitions import PolicyDefinition


class _CapturedReader:
    def __init__(self, reader):
        self.reader = reader
        self.hashes = []

    def read(self, file_id):
        text = self.reader.read(file_id)
        self.hashes.append(sha256(text.encode('utf-8')))
        return text


class ReportService:
    def __init__(self, binding: ReportBinding, policy: PolicyDefinition,
                 reader: SampleReadBinding, read_policy: PolicyDefinition):
        self.binding, self.policy = binding, policy
        self.reader, self.read_policy = reader, read_policy

    def _capture(self, query):
        if type(self.reader) is not SampleReadBinding:
            raise report_error('access_denied')
        captured = _CapturedReader(self.reader.reader)
        result = preview_evidence(query, SampleReadBinding(self.reader.manifest, captured), self.read_policy)
        return result, tuple(captured.hashes)

    def review(self, query: str, operator: str) -> dict:
        try:
            authorize_report(self.binding, self.policy)
            token(operator, r'[a-z][a-z0-9-]{0,31}')
            report, sources = self._capture(query)
            if sources != SOURCE_HASHES:
                raise report_error('source_changed')
            return self.binding.store.review(query.strip(), operator, report, int(time.time()))
        except Exception as error:
            raise safe_report_error(error, 'invalid_request') from None

    def status(self, run_id: str, operator: str, proposal_sha256: str) -> dict:
        authorize_report(self.binding, self.policy)
        try:
            return self.binding.store.load(run_id, operator, proposal_sha256)
        except Exception as error:
            raise safe_report_error(error) from None

    def decide(self, run_id: str, operator: str, proposal_sha256: str, decision: str) -> dict:
        authorize_report(self.binding, self.policy)
        try:
            return self.binding.store.decide(run_id, operator, proposal_sha256, decision, int(time.time()))
        except Exception as error:
            raise safe_report_error(error) from None

    def publish(self, run_id: str, operator: str, proposal_sha256: str) -> dict:
        record = self.status(run_id, operator, proposal_sha256)
        try:
            # Re-read only the fixed synthetic catalog, never regenerate frozen output.
            try:
                _, sources = self._capture(record['proposal']['arguments']['query'])
            except Exception:
                sources = None
            return self.binding.store.publish(run_id, operator, proposal_sha256, int(time.time()), sources)
        except Exception as error:
            raise safe_report_error(error, 'unresolved') from None
