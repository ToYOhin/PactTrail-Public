"""Current-run feedback from sampled lifecycle facts, without probing resources."""
from __future__ import annotations

import re


def project_run_feedback(state, *, stop_accepted=False, worker_busy=False, resources=()):
    """Keep logical stop, physical cleanup and process-wide billing independent.

    Resource samples are supplied by their existing owner, never polled here.
    Historical records and projections without a matching current run stay absent.
    """
    task, operation = state.get('task') or {}, state.get('operation') or {}
    run_id = task.get('run_id')
    if (type(run_id) is not str or re.fullmatch('[a-f0-9]{32}', run_id) is None
            or operation.get('id') != run_id
            or operation.get('status') not in ('running', 'complete', 'stopped', 'error')):
        return None
    if 'unknown' in resources:
        resource_state = 'unknown'
    elif worker_busy or 'active' in resources:
        resource_state = 'active' if operation['status'] == 'running' else 'draining'
    else:
        resource_state = 'released'
    usage = task.get('model')
    if not isinstance(usage, dict):
        cost = 'not_applicable'
    elif usage.get('cost_known') is True:
        cost = 'known'
    elif usage.get('cost_known') is False:
        cost = 'pending' if operation['status'] == 'running' else 'unknown'
    else:
        cost = 'unknown'
    return dict(version=1, run_id=run_id, stop_accepted=bool(stop_accepted),
                resources=resource_state, cost=cost, cost_scope='process')
