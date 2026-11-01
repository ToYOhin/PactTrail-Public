from __future__ import annotations

import json

from pacttrail.domain.evidence import (
    MAX_FILE_BYTES, MAX_MATCHES, MAX_OUTPUT_BYTES, MAX_QUERY_CHARACTERS,
    MAX_TOTAL_BYTES, SAMPLE_FILES, SampleReadBinding, SampleReadRequest,
    authorize_sample_read, evidence_error, safe_evidence_error, sample_read_manifest,
)
from pacttrail.domain.policy_definitions import PolicyDefinition


def _query(value: str) -> str:
    if (type(value) is not str or not 1 <= len(value) <= MAX_QUERY_CHARACTERS
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise evidence_error("invalid_request")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise evidence_error("invalid_request") from None
    normalized = value.strip()
    if not normalized:
        raise evidence_error("invalid_request")
    return normalized


def preview_evidence(
    query: str, binding: SampleReadBinding, policy: PolicyDefinition,
) -> str:
    """Return a complete bounded JSON document; never persist partial evidence."""
    query = _query(query)
    requests = tuple(SampleReadRequest(file_id, sample_read_manifest().definition_ref)
                     for file_id in SAMPLE_FILES)
    for request in requests:
        authorize_sample_read(request, binding, policy)

    matches: list[dict[str, object]] = []
    total_bytes = 0
    for request in requests:
        try:
            text = binding.reader.read(request.file_id)
            if type(text) is not str:
                raise evidence_error("invalid_source")
            try:
                byte_count = len(text.encode("utf-8"))
            except UnicodeError:
                raise evidence_error("invalid_source") from None
            total_bytes += byte_count
            if byte_count > MAX_FILE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise evidence_error("input_limit")
        except Exception as error:
            raise safe_evidence_error(error, "source_unavailable") from None
        for number, line in enumerate(text.splitlines(), start=1):
            if query in line:
                matches.append({"source": request.file_id, "line_start": number,
                                "line_end": number, "text": line})
                if len(matches) > MAX_MATCHES:
                    raise evidence_error("match_limit")

    payload = {
        "schema_version": "pacttrail.evidence-preview.v1",
        "dataset": "pacttrail.synthetic-evidence.v1",
        "query": query,
        "status": "matched" if matches else "missing_evidence",
        "files_checked": list(SAMPLE_FILES),
        "matches": matches,
        "authorization": {"decision": "allow",
                          "tool_ref": sample_read_manifest().definition_ref.to_dict(),
                          "policy_ref": policy.definition_ref.to_dict()},
    }
    try:
        result = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        byte_count = len(result.encode("utf-8"))
    except Exception:
        raise evidence_error("output_unavailable") from None
    if byte_count > MAX_OUTPUT_BYTES:
        raise evidence_error("output_limit")
    return result
