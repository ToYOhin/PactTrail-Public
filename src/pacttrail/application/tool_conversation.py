"""Durable dialogue facts, independent of old task executors and provider state."""
from __future__ import annotations

import copy
import secrets

from pacttrail.application.chat import chat_error
from pacttrail.domain.conversation_capacity import ConversationCapacity
from pacttrail.domain.runs import PactTrailError
from pacttrail.domain.tool_conversation import (
    validate_conversation, validate_document, validate_history_selection, make_context, context_size,
    MAX_CONVERSATIONS, MAX_ARCHIVED_CONVERSATIONS,
)


class ToolConversation:
    def __init__(self, store, *, capacity=None):
        if capacity is not None and type(capacity) is not ConversationCapacity:
            raise chat_error('invalid_request')
        self._capacity = capacity
        self._limits = capacity or ConversationCapacity()
        self.store = store
        self.failed = False
        self.error = None
        self._result = None
        self._preview = None
        try:
            saved = store.load()
            self.restored = saved is not None
            if saved is None:
                cid = secrets.token_hex(16)
                self.document = dict(format='pacttrail.tool-conversations', version=1,
                                     selected=cid, revision=0, conversations=[self._empty(cid)])
            else:
                self.document = validate_document(saved)
                self.document['revision'] += 1
                for convo in self._all_conversations():
                    if 'artifact_reference' in convo:
                        convo['artifact_reference']['state'] = 'review_required'
                    for turn in convo['turns']:
                        if turn['status'] == 'running':
                            turn.update(status='stopped', error='interrupted', answer=None)
            self.document = validate_document(self.document)
            store.save(self.document)
        except Exception:
            store.close()
            raise

    @staticmethod
    def _empty(cid):
        return dict(id=cid, title='新的对话', project=None, scope_state='empty', turns=[])

    @property
    def capacity(self):
        return self._capacity

    @property
    def revision(self):
        return self.document['revision']

    def restore(self):
        return copy.deepcopy(self._selected())

    def artifact_lineage_for_record(self, record):
        """Read a legacy relationship only when one saved turn proves this run."""
        if self.failed:
            return dict(state='unrecorded', reference=None)
        turns = [turn for convo in self._all_conversations() for turn in convo['turns']
                 if turn['run_id'] == record['run_id']]
        if not turns:
            return dict(state='unrecorded', reference=None)
        if len(turns) != 1:
            return dict(state='conflict', reference=None)
        turn, receipt = turns[0], record['task']['artifact']
        artifact = turn['artifact']
        if (turn['goal'] != record['goal'] or receipt is None or artifact is None
                or any(artifact.get(key) != receipt[key] for key in ('name', 'sha256', 'bytes'))):
            return dict(state='conflict', reference=None)
        reference = turn.get('artifact_reference')
        if reference is not None and reference['run_id'] == record['run_id']:
            return dict(state='conflict', reference=None)
        return dict(state='none' if reference is None else 'legacy', reference=copy.deepcopy(reference))

    def _selected(self, document=None):
        doc = self.document if document is None else document
        return next(item for item in doc['conversations'] if item['id'] == doc['selected'])

    def _all_conversations(self, document=None):
        doc = self.document if document is None else document
        return doc['conversations'] + doc.get('archived_conversations', [])

    def _commit(self, document):
        if self.failed:
            raise chat_error('history_unavailable')
        document = validate_document(document)
        try:
            self.store.save(document)
        except Exception:
            self.failed, self.error = True, 'history_unavailable'
            raise chat_error('history_unavailable') from None
        self.document = document

    def context(self):
        if self.failed:
            raise chat_error('history_unavailable')
        return make_context(self._selected(), max_bytes=self._limits.max_context_bytes)

    def selection_binding(self):
        """None means the default; an empty list explicitly references no history."""
        selection = self._selected().get('history_selection')
        if selection is None:
            return None
        wanted = set(selection['run_ids'])
        return [turn['run_id'] for turn in self._selected()['turns']
                if turn['status'] == 'complete' and turn['run_id'] in wanted]

    def check_history_binding(self, value):
        if value is not None:
            try:
                validate_history_selection(dict(version=1, run_ids=value))
            except PactTrailError:
                raise chat_error('invalid_request') from None
        if value != self.selection_binding():
            raise chat_error('stale_request')

    def set_history_selection(self, run_ids):
        """Change this conversation's next reference set without changing its facts."""
        convo = self._selected()
        if run_ids is not None:
            available = [turn['run_id'] for turn in convo['turns'] if turn['status'] == 'complete']
            try:
                validate_history_selection(dict(version=1, run_ids=run_ids), available=available)
            except PactTrailError:
                raise chat_error('invalid_request') from None
            wanted = set(run_ids)
            run_ids = [run for run in available if run in wanted]
        doc = copy.deepcopy(self.document)
        chosen = self._selected(doc)
        if run_ids is None:
            chosen.pop('history_selection', None)
        else:
            chosen['history_selection'] = dict(version=1, run_ids=run_ids)
        doc['revision'] += 1
        self._commit(doc)

    def read_history_turn(self, run_id):
        """Read the complete original of a successful turn in this conversation only."""
        if self.failed:
            raise chat_error('history_unavailable')
        turn = next((turn for turn in self._selected()['turns']
                     if turn['run_id'] == run_id and turn['status'] == 'complete'), None)
        if turn is None:
            raise chat_error('invalid_request')
        return copy.deepcopy(turn)

    def summary_candidate(self, run_id):
        """Extract local original text for editing; this never adopts anything."""
        from pacttrail.domain.context_summary import MAX_TEXT_BYTES, binding_for, selected_turns
        turn = self.read_history_turn(run_id)
        if run_id not in {item['run_id'] for item in selected_turns(self._selected())}:
            raise chat_error('invalid_request')
        text = turn['goal'] + '\n\n' + turn['answer']
        if turn['artifact'] is not None:
            text += '\n\n' + turn['artifact']['text']
        raw = text.encode('utf-8')
        extract = raw[:MAX_TEXT_BYTES].decode('utf-8', errors='ignore')
        if not extract.strip():
            # A valid long goal may start with more than 2 KiB of whitespace.
            # Keep the original intact while giving its candidate editable text.
            extract = text.lstrip().encode('utf-8')[:MAX_TEXT_BYTES].decode('utf-8', errors='ignore')
        return dict(version=1, binding=binding_for(self._selected(), turn),
                    text=extract,
                    original_bytes=len(raw), truncated=len(raw) > MAX_TEXT_BYTES)

    def summary_plan(self):
        from pacttrail.domain.context_summary import summary_plan
        if self.failed:
            raise chat_error('history_unavailable')
        return summary_plan(self._selected())

    def summary_binding(self):
        return self.summary_plan()['binding']

    def check_summary_binding(self, value):
        import re
        if value is not None and (type(value) is not str or re.fullmatch('[a-f0-9]{64}', value) is None):
            raise chat_error('invalid_request')
        plan = self.summary_plan()
        if value != plan['binding'] or plan['state'] == 'stale':
            raise chat_error('stale_request')

    def adopt_summary(self, binding, text):
        from pacttrail.domain.context_summary import (
            MAX_RECORDS, binding_for, selected_turns, text_digest, validate_binding,
        )
        try:
            validate_binding(binding)
            text_sha256 = text_digest(text)
        except (PactTrailError, TypeError, ValueError):
            raise chat_error('invalid_request') from None
        convo = self._selected()
        turn = next((item for item in selected_turns(convo) if item['run_id'] == binding['run_id']), None)
        if turn is None or binding != binding_for(convo, turn):
            raise chat_error('stale_request')
        doc = copy.deepcopy(self.document)
        ledger = self._selected(doc).setdefault('context_summaries', dict(version=1, records=[], active_ids=[]))
        if len(ledger['records']) >= MAX_RECORDS:
            raise chat_error('history_limit')
        version = 1 + max((item['summary_version'] for item in ledger['records']
                           if item['run_id'] == binding['run_id']), default=0)
        record = dict(**copy.deepcopy(binding), id=secrets.token_hex(16), summary_version=version,
                      text=text, text_sha256=text_sha256)
        old_ids = {item['id'] for item in ledger['records'] if item['run_id'] == binding['run_id']}
        ledger['records'].append(record)
        ledger['active_ids'] = [item for item in ledger['active_ids'] if item not in old_ids] + [record['id']]
        doc['revision'] += 1
        self._commit(doc)
        return copy.deepcopy(record)

    def withdraw_summary(self, summary_id):
        ledger = self._selected().get('context_summaries')
        if ledger is None or summary_id not in ledger['active_ids']:
            raise chat_error('stale_request')
        doc = copy.deepcopy(self.document)
        self._selected(doc)['context_summaries']['active_ids'].remove(summary_id)
        doc['revision'] += 1
        self._commit(doc)

    def prepare(self, run_id, request_revision, goal, intent, source):
        context = self.context()
        doc = copy.deepcopy(self.document)
        convo = self._selected(doc)
        if len(convo['turns']) >= self._limits.max_turns:
            raise chat_error('turn_limit')
        if any(turn['run_id'] == run_id for item in self._all_conversations(doc) for turn in item['turns']):
            raise chat_error('request_conflict')
        if (convo['scope_state'] == 'review_required'
                or convo.get('artifact_reference', {}).get('state') == 'review_required'):
            raise chat_error('stale_request')
        if not convo['turns']:
            convo['title'] = ' '.join(goal.replace('\ufeff', '').split())[:26]
        convo['turns'].append(dict(run_id=run_id, request_revision=request_revision,
            goal=goal, intent=intent, status='running', error=None, answer=None,
            source=copy.deepcopy(source), artifact=None, application=None))
        if 'history_selection' in convo:
            convo['turns'][-1]['history_selection'] = dict(version=1, run_ids=self.selection_binding())
        if 'artifact_reference' in convo:
            from pacttrail.domain.artifact_reference import reference_identity
            convo['turns'][-1]['artifact_reference'] = reference_identity(convo['artifact_reference'])
        if context.get('derived_summaries'):
            convo['turns'][-1]['derived_summaries'] = copy.deepcopy(context['derived_summaries'])
        self._commit(doc)
        self._result = None
        return context

    def finish(self, run_id, status, error, answer, artifact, web_search=None):
        doc = copy.deepcopy(self.document)
        turns = [turn for convo in doc['conversations'] for turn in convo['turns']
                 if turn['run_id'] == run_id]
        if not turns or turns[0]['status'] != 'running':
            return
        turns[0].update(status=status, error=error, answer=answer if status == 'complete' else None,
                        artifact=copy.deepcopy(artifact))
        if web_search is not None and status == 'complete':
            if turns[0]['intent'] != 'answer':
                raise chat_error('invalid_request')
            from pacttrail.domain.web_search import validate_search
            turns[0]['web_search'] = validate_search(web_search)
        self._commit(doc)

    def set_scope(self, snapshot, *, preview=False):
        doc = copy.deepcopy(self.document)
        convo = self._selected(doc)
        convo.update(project=copy.deepcopy(snapshot), scope_state=(
            'empty' if snapshot is None else 'review_required' if preview else 'confirmed'))
        doc['revision'] += 1
        self._commit(doc)
        self._preview = (convo['id'], snapshot['sha256']) if preview else None

    def confirm(self, digest):
        convo = self._selected()
        if (self._preview != (convo['id'], digest) or convo['scope_state'] != 'review_required'
                or convo['project']['sha256'] != digest):
            raise chat_error('stale_request')
        self.set_scope(convo['project'])

    def select(self, cid):
        if cid not in {item['id'] for item in self.document['conversations']}:
            raise chat_error('invalid_request')
        doc = copy.deepcopy(self.document)
        doc.update(selected=cid, revision=doc['revision'] + 1)
        self._commit(doc)
        self._preview = self._result = None

    def rename(self, title):
        selected = self._selected()
        if (not selected['turns'] or type(title) is not str
                or any(ord(c) < 32 or ord(c) == 127 for c in title)):
            raise chat_error('invalid_request')
        candidate = copy.deepcopy(selected)
        candidate['title'] = title.strip()
        try:
            validate_conversation(candidate)
        except PactTrailError:
            raise chat_error('invalid_request') from None
        doc = copy.deepcopy(self.document)
        self._selected(doc)['title'] = candidate['title']
        doc['revision'] += 1
        self._commit(doc)

    def save_input(self, expected_id, message, task_intent, creation_path):
        from pacttrail.domain.saved_input import source_identity, validate_saved_input
        self._saved_input(expected_id, required=False)
        candidate = dict(id=secrets.token_hex(16), message=message, task_intent=task_intent,
                         creation_path=creation_path, source=source_identity(self._selected()['project']))
        try:
            candidate = validate_saved_input(candidate)
        except PactTrailError:
            raise chat_error('invalid_request') from None
        doc = copy.deepcopy(self.document)
        self._selected(doc)['saved_input'] = candidate
        self._commit(doc)

    def _saved_input(self, expected_id, *, required=True):
        import re
        if expected_id is not None and (type(expected_id) is not str
                or re.fullmatch('[a-f0-9]{32}', expected_id) is None):
            raise chat_error('invalid_request')
        saved = self._selected().get('saved_input')
        if (saved['id'] if saved else None) != expected_id or required and saved is None:
            raise chat_error('stale_request')
        return saved

    def restore_input(self, expected_id):
        self._saved_input(expected_id)
        self.prepare_continuation()

    def prepare_continuation(self):
        """Prepare editable input without restoring old source permission."""
        doc = copy.deepcopy(self.document)
        convo = self._selected(doc)
        if convo['project'] is not None:
            convo['scope_state'] = 'review_required'
        if 'artifact_reference' in convo:
            convo['artifact_reference']['state'] = 'review_required'
        doc['revision'] += 1
        self._commit(doc)
        self._preview = None

    def set_artifact_reference(self, reference):
        """A trusted registered-record reader supplies the verified snapshot."""
        from pacttrail.domain.artifact_reference import validate_reference
        candidate = validate_reference(reference)
        candidate['state'] = 'confirmed'
        doc = copy.deepcopy(self.document)
        convo = self._selected(doc)
        convo['artifact_reference'] = candidate
        if convo['project'] is not None:
            convo['scope_state'] = 'review_required'
        doc['revision'] += 1
        self._commit(doc)
        self._preview = None

    def remove_artifact_reference(self, digest):
        reference = self._selected().get('artifact_reference')
        if reference is None or reference['artifact']['sha256'] != digest:
            raise chat_error('stale_request')
        doc = copy.deepcopy(self.document)
        del self._selected(doc)['artifact_reference']
        doc['revision'] += 1
        self._commit(doc)

    def delete_input(self, expected_id):
        self._saved_input(expected_id)
        doc = copy.deepcopy(self.document)
        del self._selected(doc)['saved_input']
        self._commit(doc)

    def new(self, cid):
        if len(self.document['conversations']) >= MAX_CONVERSATIONS:
            raise chat_error('history_limit')
        doc = copy.deepcopy(self.document)
        doc.update(selected=cid, revision=doc['revision'] + 1)
        doc['conversations'].append(self._empty(cid))
        self._commit(doc)
        self._preview = self._result = None

    @staticmethod
    def _require_source_review(conversation):
        if conversation['project'] is not None:
            conversation['scope_state'] = 'review_required'
        if 'artifact_reference' in conversation:
            conversation['artifact_reference']['state'] = 'review_required'

    def archive_current(self):
        """Move complete facts out of the active list, never discard them."""
        if len(self.document.get('archived_conversations', [])) >= MAX_ARCHIVED_CONVERSATIONS:
            raise chat_error('history_limit')
        if any(turn['status'] == 'running' for turn in self._selected()['turns']):
            raise chat_error('busy')
        doc = copy.deepcopy(self.document)
        selected = self._selected(doc)
        self._require_source_review(selected)
        doc.setdefault('archived_conversations', []).append(selected)
        doc['conversations'] = [item for item in doc['conversations'] if item['id'] != doc['selected']]
        if not doc['conversations']:
            doc['conversations'].append(self._empty(secrets.token_hex(16)))
        doc.update(selected=doc['conversations'][0]['id'], revision=doc['revision'] + 1)
        self._commit(doc)
        self._preview = self._result = None

    def read_archive(self, archived_id):
        """Return saved facts only; reading cannot select or restore authority."""
        if self.failed:
            raise chat_error('history_unavailable')
        item = next((item for item in self.document.get('archived_conversations', [])
                     if item['id'] == archived_id), None)
        if item is None:
            raise chat_error('invalid_request')
        return copy.deepcopy(item)

    def restore_archive(self, archived_id):
        """Restore to the active list while retaining the current selection."""
        if len(self.document['conversations']) >= MAX_CONVERSATIONS:
            raise chat_error('history_limit')
        selected = self.read_archive(archived_id)
        self._require_source_review(selected)
        doc = copy.deepcopy(self.document)
        doc['conversations'].append(selected)
        doc['archived_conversations'] = [item for item in doc['archived_conversations'] if item['id'] != archived_id]
        doc['revision'] += 1
        self._commit(doc)

    def delete(self):
        doc = copy.deepcopy(self.document)
        doc['conversations'] = [item for item in doc['conversations'] if item['id'] != doc['selected']]
        if not doc['conversations']:
            doc['conversations'].append(self._empty(secrets.token_hex(16)))
        doc.update(selected=doc['conversations'][0]['id'], revision=doc['revision'] + 1)
        self._commit(doc)
        self._preview = self._result = None

    def show(self, run_id):
        turn = next((turn for turn in self._selected()['turns'] if turn['run_id'] == run_id
                     and turn['status'] == 'complete' and turn['artifact'] is not None), None)
        if turn is None:
            raise chat_error('record_report_unavailable')
        self._result = run_id

    def prepare_application(self, run_id, digest):
        convo = self._selected()
        turn = next((turn for turn in convo['turns'] if turn['run_id'] == run_id), None)
        if (convo['scope_state'] != 'confirmed' or turn is None
                or turn['intent'] != 'change_draft' or turn['status'] != 'complete'
                or turn['artifact']['sha256'] != digest
                or turn['source']['sha256'] != convo['project']['sha256']):
            raise chat_error('stale_request')
        self.set_scope(convo['project'], preview=True)
        self._preview = None  # A write is not a fresh file preview.

    def note_application(self, receipt):
        doc = copy.deepcopy(self.document)
        convo = self._selected(doc)
        turn = next((turn for turn in convo['turns'] if turn['run_id'] == receipt['run_id']), None)
        if turn is None:
            raise chat_error('invalid_request')
        turn['application'] = copy.deepcopy(receipt)
        self._commit(doc)
        self._preview = None

    def created_paths(self, target_root):
        """Grant only applied creations from this exact scope and configured root."""
        convo = self._selected()
        project = convo['project']
        if project is None or target_root is None:
            return ()
        paths = []
        for turn in convo['turns']:
            receipt = turn['application']
            if (turn['status'] != 'complete' or turn['intent'] != 'change_draft'
                    or turn['source']['sha256'] != project['sha256'] or receipt is None
                    or receipt['state'] != 'applied' or receipt['target_root'] != target_root):
                continue
            from pacttrail.domain.change_draft import validate_draft
            draft = validate_draft(turn['artifact']['text'], turn['run_id'], turn['source'])
            if draft['version'] == 2:
                paths.append(draft['changes'][0]['path'])
        return tuple(paths)

    def project(self, *, can_refresh=False):
        convo = self._selected()
        error = self.error
        used = context_size(convo)
        if not self.failed and error is None and used > self._limits.max_context_bytes:
            error = 'context_limit'
        turn = next((turn for turn in convo['turns'] if turn['run_id'] == self._result), None)
        snapshot = convo['project']
        binding = self.selection_binding()
        selected = set(binding) if binding is not None else None
        candidates = [dict(run_id=item['run_id'], title=item['goal'][:26], intent=item['intent'],
                           has_artifact=item['artifact'] is not None,
                           artifact_sha256=item['artifact']['sha256'] if item['artifact'] else None)
                      for item in convo['turns'] if item['status'] == 'complete']
        chosen_ids = [item['run_id'] for item in candidates
                      if selected is None or item['run_id'] in selected]
        result = dict(version=1, persistent=True, selected=convo['id'],
            conversations=[dict(id=item['id'], title=item['title'], turns=len(item['turns']))
                           for item in self.document['conversations']],
            archive=dict(version=1, max_conversations=MAX_ARCHIVED_CONVERSATIONS,
                conversations=[dict(id=item['id'], title=item['title'], turns=len(item['turns']))
                               for item in self.document.get('archived_conversations', [])]),
            scope=dict(state=convo['scope_state'], name=snapshot['name'] if snapshot else None,
                       sha256=snapshot['sha256'] if snapshot else None,
                       can_refresh=bool(can_refresh and snapshot),
                       can_confirm=bool(snapshot and self._preview == (convo['id'], snapshot['sha256']))),
            context_turns=len(chosen_ids),
            history_selection=dict(version=1, mode='all' if binding is None else 'selected', run_ids=chosen_ids),
            history_candidates=candidates,
            max_turns=self._limits.max_turns, restored=self.restored,
            results=[dict(run_id=item['run_id'], title=item['goal'][:26],
                          kind='change_draft' if item['intent'] == 'change_draft' else 'report')
                     for item in convo['turns'] if item['status'] == 'complete' and item['artifact']],
            result=(dict(run_id=turn['run_id'], source=copy.deepcopy(turn['source']),
                         artifact=copy.deepcopy(turn['artifact']), application=copy.deepcopy(turn['application']))
                    if turn else None), error=error)
        if self._capacity is not None:
            result.update(max_context_bytes=self._limits.max_context_bytes,
                          context_bytes=used, overflow_policy='reject')
        if 'saved_input' in convo:
            result['saved_input'] = copy.deepcopy(convo['saved_input'])
        if 'artifact_reference' in convo:
            result['artifact_reference'] = copy.deepcopy(convo['artifact_reference'])
        if convo.get('context_summaries', {}).get('records'):
            from pacttrail.domain.context_summary import summary_plan
            result['summary_plan'] = summary_plan(convo)
            if result['summary_plan']['state'] == 'stale' and result['error'] is None:
                result['error'] = 'stale_request'
        return result

    def close(self):
        self.store.close()
