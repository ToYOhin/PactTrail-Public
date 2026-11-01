"""Versioned offline conversation records; no provider or executable state."""
from __future__ import annotations

import copy
import json
import secrets

from pacttrail.application.evidence_preview import preview_evidence
from pacttrail.application import ordinary_chat, chat_material

FORMAT = 'pacttrail.offline-history'
MAX_CONVERSATIONS = 50


def _title(value, *, empty=False):
    return (type(value) is str and (empty or ordinary_chat.valid_text(value, 80)) and len(value) <= 80
            and not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) == 127 for c in value))


def validate_history(data, sources, *, answer_mode='evidence'):
    """Validate closed data shapes and relational consistency before restoring."""
    from pacttrail.application.chat import PROMPTS, LIMITS, _CODES, _identifier, _bytes, chat_error

    def require(condition):
        if not condition:
            raise chat_error('history_corrupt')

    def shape(value, keys):
        require(type(value) is dict and set(value) == set(keys.split()))

    def text(value, limit):
        require(type(value) is str and len(value) <= limit)
        value.encode('utf-8')

    try:
        shape(data, 'format version revision selected seen conversations')
        ordinary = answer_mode == 'ordinary'
        require(answer_mode in ('evidence', 'ordinary'))
        require(data['format'] == (ordinary_chat.FORMAT if ordinary else FORMAT)
                and type(data['version']) is int and data['version'] in ((1, 2) if ordinary else (1,)))
        materials = ordinary and data['version'] == 2
        require(type(data['revision']) is int and 0 <= data['revision'] < 2147483647)
        require(_identifier(data['selected']))
        require(type(data['seen']) is list and len(data['seen']) <= LIMITS['max_requests'])
        seen = {}
        for entry in data['seen']:
            shape(entry, 'id conversation_id revision message')
            require(_identifier(entry['id']) and entry['id'] not in seen)
            require(_identifier(entry['conversation_id']))
            require(type(entry['revision']) is int and 0 <= entry['revision'] <= data['revision'])
            require(ordinary_chat.valid_text(entry['message'], LIMITS['max_message_chars']) if ordinary
                    else type(entry['message']) is str and entry['message'] in PROMPTS)
            seen[entry['id']] = entry
        require(type(data['conversations']) is list and 1 <= len(data['conversations']) <= MAX_CONVERSATIONS)
        ids, used = set(), set()
        for record in data['conversations']:
            shape(record, 'id title messages context operation' + (' material' if materials else ''))
            if materials and record['material'] is not None:
                chat_material.validate_saved(record['material'])
            cid = record['id']
            require(_identifier(cid) and cid not in ids and _title(record['title']))
            ids.add(cid)
            require(type(record['messages']) is list and len(record['messages']) <= 12)
            turns, context, previous = 0, [], None
            for message in record['messages']:
                shape(message, 'role text status sources operation_id')
                rid = message['operation_id']
                require(_identifier(rid) and rid in seen and seen[rid]['conversation_id'] == cid)
                require(type(message['role']) is str and message['role'] in ('user', 'assistant'))
                require(type(message['status']) is str and message['status'] in ('complete', 'stopped', 'error'))
                text(message['text'], 32768)
                require(type(message['sources']) is list and len(message['sources']) <= 3)
                if message['role'] == 'user':
                    require(rid not in used and message['text'] == seen[rid]['message'] and message['sources'] == [])
                    # Only the final question can be incomplete; no success is fabricated.
                    require(previous is None or previous['role'] == 'assistant' or previous['status'] in ('error', 'stopped'))
                    used.add(rid)
                    turns += 1
                else:
                    require(previous is not None and previous['role'] == 'user'
                            and previous['operation_id'] == rid and previous['status'] == 'complete'
                            and message['status'] == 'complete')
                    if ordinary:
                        require(message['sources'] == [] and ordinary_chat.valid_text(
                            message['text'], ordinary_chat.MAX_ANSWER_CHARS))
                        require(len(message['text'].encode('utf-8')) <= ordinary_chat.MAX_ANSWER_BYTES)
                        context.extend([{'role': 'user', 'content': previous['text']},
                                        {'role': 'assistant', 'content': message['text']}])
                    elif message['sources']:
                        require(message['sources'] == sources)
                        context.extend([{'role': 'user', 'content': previous['text']},
                                        {'role': 'assistant', 'content': message['text']}])
                    else:
                        require(previous['text'] in (PROMPTS[3], PROMPTS[4], 'missing-topic'))
                previous = message
            require(turns <= LIMITS['max_turns'])
            require(turns == sum(entry['conversation_id'] == cid for entry in seen.values()))
            require(record['context'] == context and _bytes(context) <= LIMITS['max_context_bytes'])
            operation = record['operation']
            if previous is None:
                require(operation is None)
            else:
                shape(operation, 'id status error')
                require(operation['id'] == previous['operation_id'])
                require(type(operation['status']) is str and operation['status'] in ('running', 'complete', 'stopped', 'error'))
                require(operation['error'] is None or (type(operation['error']) is str and operation['error'] in _CODES))
                require((operation['status'] != 'running' or operation['error'] is None)
                        and (operation['status'] != 'error' or operation['error'] is not None)
                        and (operation['status'] != 'stopped' or operation['error'] in (None, 'interrupted')))
                if previous['role'] == 'assistant':
                    require(operation['status'] == 'complete' and operation['error'] is None)
                else:
                    require(operation['status'] != 'complete')
                    expected = 'complete' if operation['status'] == 'running' else operation['status']
                    require(previous['status'] == expected)
            # Material v2 also stores a text snapshot; messages and context overlap.
            require(_bytes(record) <= (96 * 1024 if materials else 55000))
        require(data['selected'] in ids and _bytes(data) <= 2 * 1024 * 1024)
        return copy.deepcopy(data)
    except Exception:
        raise chat_error('history_corrupt') from None


class HistoryMixin:
    """Narrow persistence integrated under ChatSession's existing shared lock."""

    def _initialize_history(self, history):
        self._history = history
        self._history_version = 1
        self._history_failed = False
        self._records = {}
        self._history_query = ''
        if history is None:
            return
        try:
            saved = history.load()
            if saved is None:
                self._records[self._conversation_id] = self._empty_record()
            else:
                from pacttrail.application.chat import chat_error
                expected = ordinary_chat.FORMAT if self._answer_mode == 'ordinary' else FORMAT
                if (type(saved) is dict and saved.get('format') in (FORMAT, ordinary_chat.FORMAT)
                        and saved['format'] != expected):
                    raise chat_error('history_mode')
                # Recheck only the three authorized fixed sources, never workspace files.
                sources = ([] if self._answer_mode == 'ordinary' else
                           json.loads(preview_evidence('approval', self._binding, self._policy))['matches'])
                saved = validate_history(saved, sources, answer_mode=self._answer_mode)
                self._history_version = saved['version']
                self._records = {r['id']: r for r in saved['conversations']}
                self._seen = {r['id']: (r['conversation_id'], r['revision'], r['message']) for r in saved['seen']}
                self._revision = saved['revision'] + 1
                for record in self._records.values():
                    if record['operation'] and record['operation']['status'] == 'running':
                        record['operation'].update(status='stopped', error='interrupted')
                        record['messages'][-1]['status'] = 'stopped'
                self._load_record(saved['selected'])
            self._persist()
        except Exception as error:
            history.close()
            from pacttrail.application.chat import safe_chat_error
            raise safe_chat_error(error, 'history_unavailable') from None

    def _empty_record(self):
        record = {'id': self._conversation_id, 'title': '新的对话', 'messages': [], 'context': [], 'operation': None}
        if self._answer_mode == 'ordinary' and self._history_version == 2:
            record['material'] = None
        return record

    def _capture_current(self):
        record = self._records[self._conversation_id]
        record.update(messages=self._messages, context=self._context, operation=self._operation)
        if self._answer_mode == 'ordinary' and self._history_version == 2:
            record['material'] = self._material

    def _load_record(self, cid):
        self._conversation_id = cid
        record = self._records[cid]
        self._messages, self._context, self._operation = record['messages'], record['context'], record['operation']
        self._material = record.get('material')
        self._provider_id = 'offline'  # Connection/model selection is never serialized.

    def _persist(self):
        from pacttrail.application.chat import chat_error
        if self._history is None:
            return
        if self._history_failed:
            raise chat_error('history_unavailable')
        self._capture_current()
        data = {'format': ordinary_chat.FORMAT if self._answer_mode == 'ordinary' else FORMAT,
                'version': self._history_version, 'revision': self._revision, 'selected': self._conversation_id,
                'conversations': list(self._records.values()),
                'seen': [{'id': rid, 'conversation_id': identity[0], 'revision': identity[1], 'message': identity[2]}
                         for rid, identity in self._seen.items()]}
        try:
            self._history.save(data)
        except Exception:
            self._history_failed = True
            self._cancel_work()
            if self._operation and self._operation['status'] == 'running':
                self._mark('error', 'history_unavailable')
            raise chat_error('history_unavailable') from None

    def _project_history(self, result):
        from pacttrail.application.chat import chat_error
        if self._history_failed:
            raise chat_error('history_unavailable')
        if self._history is None:
            return
        self._capture_current()
        query = self._history_query.casefold()
        items = []
        for record in self._records.values():
            if not query or query in record['title'].casefold() or any(query in m['text'].casefold() for m in record['messages']):
                items.append({'id': record['id'], 'title': record['title'],
                              'turns': sum(m['role'] == 'user' for m in record['messages'])})
        result['history'] = {'location': self._history.location, 'items': items,
                             'current_title': self._records[self._conversation_id]['title'],
                             'requests_used': len(self._seen), 'max_conversations': MAX_CONVERSATIONS,
                             'query': self._history_query}

    def _leave_history(self):
        if self._operation and self._operation['status'] == 'running':
            self._mark('stopped')
        self._cancel_work()
        self._capture_current()
        self._revision += 1

    def _new_history(self, provider='offline'):
        from pacttrail.application.chat import chat_error
        if len(self._records) >= MAX_CONVERSATIONS:
            raise chat_error('history_limit')
        self._leave_history()
        self._conversation_id = secrets.token_hex(16)
        self._records[self._conversation_id] = self._empty_record()
        self._load_record(self._conversation_id)
        self._provider_id = provider
        self._history_query = ''
        self._persist()
        return self._project()

    def _history_action(self, body):
        from pacttrail.application.chat import chat_error, _identifier, _bytes
        if self._history is None:
            raise chat_error('history_unavailable')
        fields = {'new': set(), 'select': {'target_id'}, 'rename': {'title'},
                  'search': {'query'}, 'save': set(), 'delete': set(), 'export': set()}
        if (type(body) is not dict or type(body.get('action')) is not str or body['action'] not in fields
                or set(body) != {'conversation_id', 'revision', 'action'} | fields[body['action']]):
            raise chat_error('invalid_request')
        self._bound(body)
        action = body['action']
        if action == 'new':
            return self._new_history()
        if action == 'select':
            target = body['target_id']
            if not _identifier(target) or target not in self._records:
                raise chat_error('invalid_request')
            self._leave_history()
            self._load_record(target)
            self._persist()
        elif action == 'rename':
            if not _title(body['title']):
                raise chat_error('invalid_request')
            self._records[self._conversation_id]['title'] = body['title'].strip()
            self._revision += 1
            # Rename while generating would invalidate the worker's identity.
            # Stop explicitly so the saved turn cannot remain running forever.
            if self._operation and self._operation['status'] == 'running':
                self._mark('stopped')
                self._cancel_work()
            self._persist()
        elif action == 'search':
            if not _title(body['query'], empty=True):
                raise chat_error('invalid_request')
            self._history_query = body['query']
        elif action == 'delete':
            removed = self._conversation_id
            self._leave_history()
            del self._records[removed]
            if not self._records:
                self._conversation_id = secrets.token_hex(16)
                self._records[self._conversation_id] = self._empty_record()
            self._load_record(next(iter(self._records)))
            self._history_query = ''
            self._persist()
        elif action == 'save':
            self._persist()
        elif action == 'export':
            self._persist()
            exported = {'format': 'pacttrail-offline-conversation', 'version': 1, 'mode': 'offline_demo',
                        'title': self._records[self._conversation_id]['title'],
                        'conversation_id': self._conversation_id, 'messages': copy.deepcopy(self._messages)}
            if self._answer_mode == 'ordinary':
                exported['answer_mode'] = 'ordinary'
                if self._material is not None:
                    exported['material'] = copy.deepcopy(self._material)
            if _bytes(exported) > 65536:
                raise chat_error('response_limit')
            return exported
        return self._project()
