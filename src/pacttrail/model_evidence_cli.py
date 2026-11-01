"""Additive preparation command; live execution is deliberately unavailable."""
from __future__ import annotations

import sys

from pacttrail.application.model_evidence import model_evidence_error, prepare_model_evidence
from pacttrail.domain.evidence import SampleReadBinding, sample_read_manifest, sample_read_policy
from pacttrail.platform.evidence_files import SampleEvidenceReader


def add_model_evidence_parser(commands):
    parser = commands.add_parser("synthesize", help="prepare a synthetic model evidence request")
    parser.add_argument("--sample", action="store_true", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--prepare", action="store_true", help="show input only; no model call")


def run_model_evidence(args) -> int:
    if args.prepare is not True:
        raise model_evidence_error("live_scope_required")
    result = prepare_model_evidence(
        args.query, SampleReadBinding(sample_read_manifest(), SampleEvidenceReader()),
        sample_read_policy(),
    )
    try:
        binary = getattr(sys.stdout, "buffer", None)
        if binary is not None:
            encoded = result.encode("utf-8")
            writer = getattr(binary, "raw", binary)
            if writer.write(encoded) != len(encoded):
                raise model_evidence_error("output_unavailable")
        else:
            if getattr(sys.stdout, "encoding", None) is not None:
                raise model_evidence_error("output_unavailable")
            if sys.stdout.write(result) != len(result):
                raise model_evidence_error("output_unavailable")
        sys.stdout.flush()
    except Exception:
        raise model_evidence_error("output_unavailable") from None
    return 0
