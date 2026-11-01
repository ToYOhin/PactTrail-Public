from __future__ import annotations

from .evidence_report import (
    ReportProposal, digest, file_identity, parse_json, report_error, timestamp, token,
)


_PATHS = (
    ('reviewed',), ('reviewed', 'denied'), ('reviewed', 'approved'),
    ('reviewed', 'approved', 'intent'),
    ('reviewed', 'approved', 'intent', 'staging'),
    ('reviewed', 'approved', 'intent', 'staging', 'complete'),
)


def new_record(proposal: ReportProposal) -> dict:
    result = {'schema_version': 'pacttrail.evidence-report-ledger.v1',
              'proposal': proposal.to_dict(), 'proposal_sha256': digest(proposal.to_dict()),
              'state': 'reviewed', 'stage_identity': None, 'audit': []}
    transition(result, 'reviewed', proposal.created_at)
    return result


def transition(record: dict, state: str, now: int) -> None:
    record['state'] = state
    record['audit'].append({'sequence': len(record['audit']) + 1, 'state': state,
                            'at': now, 'operator': record['proposal']['operator'],
                            'proposal_sha256': record['proposal_sha256']})


def read_record(text: str) -> dict:
    record = parse_json(text)
    if (set(record) != {'schema_version', 'proposal', 'proposal_sha256', 'state',
                       'stage_identity', 'audit'}
            or record['schema_version'] != 'pacttrail.evidence-report-ledger.v1'):
        raise report_error('unresolved')
    proposal = ReportProposal.from_dict(record['proposal'])
    if record['proposal_sha256'] != digest(proposal.to_dict()):
        raise report_error('unresolved')
    audit = record['audit']
    if type(audit) is not list or not 1 <= len(audit) <= 5:
        raise report_error('unresolved')
    states = []
    previous = proposal.created_at
    for sequence, event in enumerate(audit, 1):
        if (type(event) is not dict or set(event) != {
                'sequence', 'state', 'at', 'operator', 'proposal_sha256'}
                or type(event['sequence']) is not int or event['sequence'] != sequence
                or event['operator'] != proposal.operator
                or event['proposal_sha256'] != record['proposal_sha256']):
            raise report_error('unresolved')
        now = timestamp(event['at'])
        if now < previous or (event['state'] != 'complete' and now >= proposal.created_at + 900):
            raise report_error('unresolved')
        previous = now
        states.append(event['state'])
    if tuple(states) not in _PATHS or record['state'] != states[-1]:
        raise report_error('unresolved')
    if record['state'] in ('staging', 'complete'):
        file_identity(record['stage_identity'])
    elif record['stage_identity'] is not None:
        raise report_error('unresolved')
    return record


def validate_identity(record: dict, run_id: str, operator: str, proposal_sha256: str) -> None:
    token(run_id, r'[0-9a-f]{32}')
    token(operator, r'[a-z][a-z0-9-]{0,31}')
    token(proposal_sha256, r'[0-9a-f]{64}')
    if (run_id != record['proposal']['run_id'] or operator != record['proposal']['operator']
            or proposal_sha256 != record['proposal_sha256']):
        raise report_error('access_denied')


def check_time(record: dict, now: int, *, unfinished=True) -> None:
    timestamp(now)
    if now < record['audit'][-1]['at']:
        raise report_error('expired')
    if unfinished and now >= record['proposal']['expires_at']:
        raise report_error('expired')
