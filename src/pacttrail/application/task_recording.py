"""Allowlisted execution facts, separate from model context and chat history."""
from __future__ import annotations

import copy
import time

from pacttrail.domain.task_record import (
    COST_LEDGER_FIELDS, TaskRecordError, preview_task_steps, validate_draft_rejection,
)


class TaskRecording:
    """One run's references survive a view reset, without enabling continuation."""

    def __init__(self, store, task, operation, goal, model_status, mode, *, followup_context=None,
                 artifact_reference=None):
        self.store, self.task, self.operation = store, task, operation
        self.goal, self.model_status, self.mode = goal, model_status, mode
        self.followup_context = copy.deepcopy(followup_context)
        self.artifact_reference = copy.deepcopy(artifact_reference)
        self.created_at = int(time.time())
        self.updated_at = self.created_at
        self.revision = 0
        self.pending = None
        self.failed = False

    def checkpoint(self):
        if self.failed:
            raise ValueError('record_unavailable')
        try:
            artifact = self.task['artifact']
            budget = copy.deepcopy(self.task['budget'])
            if self.model_status is None:
                cost = dict(scope='process', mode=self.mode, calls=0, reserved_nano_usd=0,
                            accounted_nano_usd=0, budget_nano_usd=0, cost_known=True,
                            stopped=False, stop_reason=None, token_basis='synthetic-no-model',
                            pricing_basis='synthetic-no-model', input_estimate_tokens=None, usage=None)
            else:
                usage = self.model_status()
                fields = ('calls', 'reserved_nano_usd', 'accounted_nano_usd', 'budget_nano_usd',
                          'cost_known', 'stopped', 'stop_reason', 'token_basis', 'pricing_basis',
                          'input_estimate_tokens', 'usage')
                cost = {key: copy.deepcopy(usage[key]) for key in fields}
                if usage.get('ledger_version') == 1:
                    cost.update({key: copy.deepcopy(usage[key]) for key in COST_LEDGER_FIELDS})
                cost.update(scope='process', mode=self.mode)
                budget.update(model_dispatches=cost['calls'], model_cost_nano=cost.get(
                    'model_accounted_nano_usd', cost['accounted_nano_usd']))
            record = dict(version=1, run_id=self.task['run_id'], revision=self.revision + 1,
                          created_at=self.created_at, updated_at=max(self.updated_at, int(time.time())),
                          goal=self.goal, status=self.operation['status'], error=self.operation['error'],
                          task=dict(workspace=self.task['workspace'], source=copy.deepcopy(self.task.get('source')),
                                    steps=preview_task_steps(self.task['steps']),
                                    artifact=None if artifact is None else
                                    {key: artifact[key] for key in ('name', 'sha256', 'bytes')}, budget=budget),
                          pending=copy.deepcopy(self.pending), cost=cost,
                          artifact_reference=copy.deepcopy(self.artifact_reference))
            if 'parent' in self.task:
                record.update(version=2, parent=copy.deepcopy(self.task['parent']))
            if self.followup_context is not None:
                record.update(version=3, followup_context=copy.deepcopy(self.followup_context))
            if self.task.get('intent') == 'change_draft':
                record['version'] = 6
                record['task']['intent'] = 'change_draft'
                record['task']['rejection'] = None
                if self.task.get('error') == record['error']:
                    try:
                        record['task']['rejection'] = validate_draft_rejection(self.task.get('rejection'),
                            status=record['status'], error=record['error'], pending=record['pending'])
                    except TaskRecordError:
                        pass
            elif self.task.get('intent') == 'answer':
                record['version'] = 5
                record['task']['intent'] = 'answer'
            self.store.write(record)
            self.updated_at = record['updated_at']
            self.revision += 1
        except Exception:
            self.failed = True
            raise
