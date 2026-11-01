from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from pacttrail.application.demo import DemoService
from pacttrail.application.evidence_preview import preview_evidence
from pacttrail.domain.evidence import (
    SampleReadBinding, evidence_error, safe_evidence_error,
    sample_read_manifest, sample_read_policy,
)
from pacttrail.domain.runs import PactTrailError
from pacttrail.platform.evidence_files import SampleEvidenceReader
from pacttrail.platform.runtime_adapter import LightAgentRuntimeAdapter
from pacttrail.domain.evidence_report import report_error
from pacttrail.report_cli import add_report_parser, run_report
from pacttrail.application.model_evidence import model_evidence_error
from pacttrail.model_evidence_cli import add_model_evidence_parser, run_model_evidence
from pacttrail.application.chat import chat_error
from pacttrail.chat_cli import add_chat_parser, run_chat
from pacttrail.agent_cli import add_agent_parser, run_agent


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if (self.prog == 'pacttrail chat' or self.prog.startswith('pacttrail chat ')
                or self.prog == 'pacttrail agent'):
            raise chat_error('invalid_request')
        if self.prog == "pacttrail evidence synthesize":
            raise model_evidence_error("invalid_request")
        if self.prog == "pacttrail evidence report" or self.prog.startswith("pacttrail evidence report "):
            raise report_error("invalid_request")
        if self.prog == "pacttrail evidence" or self.prog.startswith("pacttrail evidence "):
            raise evidence_error("invalid_request")
        raise PactTrailError(f"invalid command: {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="pacttrail",
        description="Auditable multi-agent execution with human approval.",
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("PACTTRAIL_DATA_DIR", ".pacttrail-local"),
        help="checkpoint and artifact directory (default: .pacttrail-local)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="verify the pinned runtime")
    add_chat_parser(commands)
    add_agent_parser(commands)

    evidence = commands.add_parser("evidence", help="preview fixed synthetic evidence")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    preview = evidence_commands.add_parser("preview", help="show cited sample excerpts")
    preview.add_argument("--sample", action="store_true", required=True)
    preview.add_argument("--query", required=True)
    add_report_parser(evidence_commands)
    add_model_evidence_parser(evidence_commands)

    demo = commands.add_parser("demo", help="run the deterministic offline workflow")
    demo_commands = demo.add_subparsers(dest="demo_command", required=True)

    start = demo_commands.add_parser("start", help="start and pause before execution")
    start.add_argument("--task", required=True)
    start.add_argument("--run-id")

    for decision in ("approve", "reject"):
        decision_parser = demo_commands.add_parser(
            decision,
            help=f"{decision} the waiting execute step",
        )
        decision_parser.add_argument("--run-id", required=True)

    status = demo_commands.add_parser("status", help="read a persisted run")
    status.add_argument("--run-id", required=True)
    resume = demo_commands.add_parser("resume", help="resume an interrupted run")
    resume.add_argument("--run-id", required=True)
    return parser


def _write_json(payload: object, *, stream: object | None = None) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2),
        file=stream if stream is not None else sys.stdout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parser = build_parser()
        args, unknown = parser.parse_known_args(argv)
        if unknown:
            if args.command in ('chat', 'agent'):
                raise chat_error('invalid_request')
            if args.command == "evidence" and args.evidence_command == "synthesize":
                raise model_evidence_error("invalid_request")
            if args.command == "evidence" and args.evidence_command == "report":
                raise report_error("invalid_request")
            if args.command == "evidence":
                raise evidence_error("invalid_request")
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        if args.command == 'chat':
            return run_chat(args)
        if args.command == 'agent':
            return run_agent(args)
        if args.command == "evidence":
            if args.evidence_command == "synthesize":
                return run_model_evidence(args)
            if args.evidence_command == "report":
                return run_report(args)
            try:
                result = preview_evidence(
                    args.query,
                    SampleReadBinding(sample_read_manifest(), SampleEvidenceReader()),
                    sample_read_policy(),
                )
            except Exception as error:
                raise safe_evidence_error(error, "source_unavailable") from None
            try:
                # Bypass locale encoding and newline translation for the measured
                # UTF-8 document. Text-only in-memory test streams need no encoding.
                binary = getattr(sys.stdout, "buffer", None)
                if binary is not None:
                    encoded = result.encode("utf-8")
                    # Keep our bytes out of a pending buffer: interpreter shutdown
                    # must not retry a failed pipe flush after the safe error.
                    writer = getattr(binary, "raw", binary)
                    if writer.write(encoded) != len(encoded):
                        raise evidence_error("output_unavailable")
                else:
                    if getattr(sys.stdout, "encoding", None) is not None:
                        raise evidence_error("output_unavailable")
                    if sys.stdout.write(result) != len(result):
                        raise evidence_error("output_unavailable")
                sys.stdout.flush()
            except Exception:
                raise evidence_error("output_unavailable") from None
            return 0
        data_dir = Path(args.data_dir)
        runtime = LightAgentRuntimeAdapter(data_dir)
        service = DemoService(runtime, data_dir)

        if args.command == "doctor":
            report = runtime.doctor()
            _write_json(report)
            return 0 if report["ready"] else 1

        if args.demo_command == "start":
            _write_json(service.start(args.task, run_id=args.run_id).to_dict())
        elif args.demo_command in {"approve", "reject"}:
            _write_json(
                service.decide(args.run_id, action=args.demo_command).to_dict()
            )
        elif args.demo_command == "resume":
            _write_json(service.resume(args.run_id).to_dict())
        else:
            _write_json(service.status(args.run_id).to_dict())
        return 0
    except (PactTrailError, OSError) as exc:
        _write_json(
            {"error": str(exc), "error_type": type(exc).__name__},
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
