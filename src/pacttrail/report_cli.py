"""CLI composition for the single fixed report operation; no runtime wiring."""
from __future__ import annotations

from pathlib import Path
import sys

from pacttrail.application.evidence_report import ReportService
from pacttrail.domain.definitions import canonical_json
from pacttrail.domain.evidence import SampleReadBinding, sample_read_manifest, sample_read_policy
from pacttrail.domain.evidence_report import (
    RECORD_LIMIT, ReportBinding, report_error, report_manifest, report_policy, safe_report_error,
)
from pacttrail.platform.evidence_files import SampleEvidenceReader
from pacttrail.platform.report_store import LocalReportStore


def add_report_parser(evidence_commands):
    report = evidence_commands.add_parser('report', help='review and approve a fixed synthetic report')
    commands = report.add_subparsers(dest='report_command', required=True)
    for name in ('review', 'decide', 'publish', 'resume', 'status'):
        command = commands.add_parser(name, help=f'{name} the synthetic report')
        command.add_argument('--state-dir', required=True, help='dedicated absolute directory; new for review')
        command.add_argument('--operator', required=True, help='local reviewer label, not account authentication')
        if name == 'review':
            command.add_argument('--sample', action='store_true', required=True)
            command.add_argument('--query', required=True)
        else:
            command.add_argument('--run-id', required=True)
            command.add_argument('--proposal-sha256', required=True)
        if name == 'decide':
            command.add_argument('--decision', required=True, choices=('approve', 'deny'))
    serve = commands.add_parser('serve', help='open one local synthetic report approval session')
    serve.add_argument('--state-dir', required=True)
    serve.add_argument('--operator', required=True)
    serve.add_argument('--port', type=int, default=0)
    serve.add_argument('--run-id')
    serve.add_argument('--proposal-sha256')


def run_report(args) -> int:
    try:
        service = ReportService(
            ReportBinding(report_manifest(), LocalReportStore(Path(args.state_dir))), report_policy(),
            SampleReadBinding(sample_read_manifest(), SampleEvidenceReader()), sample_read_policy(),
        )
        if args.report_command == 'serve':
            from pacttrail.report_web import serve_report
            return serve_report(args, service, Path(args.state_dir))
        if args.report_command == 'review':
            record = service.review(args.query, args.operator)
        else:
            identity = (args.run_id, args.operator, args.proposal_sha256)
            if args.report_command == 'decide':
                record = service.decide(*identity, args.decision)
            elif args.report_command in ('publish', 'resume'):
                record = service.publish(*identity)
            else:
                record = service.status(*identity)
        result = canonical_json(record) + '\n'
        encoded = result.encode('utf-8')
        if len(encoded) > RECORD_LIMIT:
            raise report_error('output_unavailable')
        try:
            binary = getattr(sys.stdout, 'buffer', None)
            if binary is not None:
                writer = getattr(binary, 'raw', binary)
                if writer.write(encoded) != len(encoded):
                    raise report_error('output_unavailable')
            elif (getattr(sys.stdout, 'encoding', None) is not None
                  or sys.stdout.write(result) != len(result)):
                raise report_error('output_unavailable')
            sys.stdout.flush()
        except Exception:
            raise report_error('output_unavailable') from None
        return 0
    except Exception as error:
        raise safe_report_error(error) from None
