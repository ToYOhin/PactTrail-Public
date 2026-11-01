"""Explicit parent selection, separate from read-only history and new execution."""
from __future__ import annotations

import copy
import hashlib

from pacttrail.application.chat import chat_error
from pacttrail.domain.runs import PactTrailError
from pacttrail.domain.task_record import TaskRecordError


class TaskFollowup:
    """Bounded preview only; the owning session alone can create a new task."""

    def __init__(self, store):
        self.store = store
        self.records = []
        self.bundle = self.context = self.selected = None
        self.error = None
        self.refresh()

    def clear(self):
        self.bundle = self.context = self.selected = None
        self.error = None

    def fail(self, code):
        self.clear()
        self.error = code

    def refresh(self):
        self.clear()
        try:
            self.records = self.store.list_records()
        except TaskRecordError as error:
            self.records = []
            self.error = error.code

    def select(self, run_id, round_count=None):
        from pacttrail.domain.task_followup import context_rounds, context_text, make_context, parent_reference
        self.clear()
        try:
            bundle = (self.store.prepare_followup(run_id) if round_count is None else
                      self.store.prepare_followup(run_id, round_count=round_count))
            # A producer may have checkpointed after the page loaded its list.
            # Publish the reference only with a matching, freshly read summary.
            self.records = self.store.list_records()
            summary = next((item for item in self.records if item['run_id'] == run_id), None)
            parent = bundle['parent']
            if (summary is None or summary['revision'] != parent['revision']
                    or summary['goal'] != parent['goal'] or summary['status'] != 'complete'
                    or summary['recovery'] != 'terminal' or summary['integrity'] != 'verified'
                    or summary['error'] is not None or not summary['has_report']):
                raise TaskRecordError('record_conflict')
            context = make_context(bundle)
            text = context_text(context)
            raw = text.encode('utf-8')
            preview = dict(binding_sha256=bundle['sha256'], parent=parent_reference(bundle),
                           context=dict(text=text, sha256=hashlib.sha256(raw).hexdigest(), byte_count=len(raw)),
                           source=copy.deepcopy(context['source']))
            if context['version'] == 2:
                preview['rounds'] = [item['parent'] for item in context_rounds(context)]
            self.bundle, self.context, self.selected = bundle, context, preview
        except TaskRecordError as error:
            self.fail(error.code)
        except PactTrailError:
            self.fail('context_limit')

    def confirm(self, binding):
        if self.bundle is None or type(binding) is not str:
            raise chat_error('stale_request')
        try:
            run_id = self.bundle['parent']['run_id']
            current = (self.store.prepare_followup(run_id, round_count=len(self.bundle['history']) + 1)
                       if self.bundle['version'] == 2 else self.store.prepare_followup(run_id))
            if binding != self.bundle['sha256'] or current != self.bundle:
                raise ValueError('changed parent')
        except Exception:
            self.fail('stale_request')
            raise chat_error('stale_request') from None

    def project(self):
        return copy.deepcopy(dict(version=1, records=self.records, selected=self.selected, error=self.error))
