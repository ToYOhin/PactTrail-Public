"""Ephemeral discovery and reviewed snapshots for one explicitly bound root.

Discovery never changes conversation scope. Only a confirmed attachment uses the
existing project action; subsequent preview/send/draft consumers remain shared.
"""
from __future__ import annotations

import base64
import copy
import re
import secrets

from pacttrail.application.chat import chat_error
from pacttrail.platform.project_locator import ProjectLocationError


class BoundProjectLocation:
    def __init__(self, locator):
        self._locator = locator
        self._binding = None
        self._result = None

    def clear(self):
        self._binding = self._result = None

    @staticmethod
    def _identity(state):
        return (state['conversation_id'], state['revision'],
                (state.get('project') or {}).get('sha256'))

    def action(self, body, state, attach):
        fields = {'search': {'query', 'mode'}, 'preview': {'search_id', 'paths'},
                  'attach': {'search_id', 'project_sha256', 'confirmed'},
                  'cancel': {'search_id'}}
        if (type(body) is not dict or type(body.get('action')) is not str
                or body['action'] not in fields
                or set(body) != {'conversation_id', 'revision', 'action'} | fields[body['action']]
                or type(body.get('revision')) is not int):
            raise chat_error('project_location_invalid')
        identity = self._identity(state)
        if body['conversation_id'] != identity[0] or body['revision'] != identity[1]:
            raise chat_error('stale_request')
        if self._binding != identity:
            self.clear()
        if state['busy']:
            raise chat_error('busy')
        action = body['action']
        if action != 'search':
            if (type(body['search_id']) is not str
                    or re.fullmatch(r'[a-f0-9]{32}', body['search_id']) is None
                    or self._result is None or body['search_id'] != self._result['search_id']):
                raise chat_error('project_location_stale')
        if action == 'cancel':
            cancelled = {key: self._result[key] for key in (
                'version', 'conversation_id', 'revision', 'target_root', 'search_id')}
            self.clear()
            return dict(kind='project_location_cancelled', **cancelled)
        if state.get('material') is not None or state.get('project') is not None:
            raise chat_error('material_exists')
        try:
            self._locator.check_root()
            if action == 'search':
                self.clear()
                result = self._locator.search(body['query'], body['mode'])
                self._binding = identity
                self._result = dict(kind='project_location', version=1,
                    conversation_id=identity[0], revision=identity[1],
                    target_root=str(self._locator.root), search_id=secrets.token_hex(16),
                    query=body['query'], mode=body['mode'], selection=None, **result)
            elif action == 'preview':
                # Retire a previous preview before attempting a different range.
                self._result['selection'] = None
                paths = body['paths']
                if (type(paths) is not list or not 1 <= len(paths) <= 4
                        or any(type(path) is not str for path in paths)
                        or len({path.casefold() for path in paths}) != len(paths)
                        or not set(paths).issubset({item['path'] for item in self._result['files']})):
                    raise chat_error('project_location_invalid')
                self._result['selection'] = self._locator.snapshot(paths)
            else:
                selection = self._result['selection']
                if (body['confirmed'] is not True or not selection
                        or body['project_sha256'] != selection['sha256']):
                    raise chat_error('project_location_stale')
                files = [dict(path=item['path'], data=base64.b64encode(
                    item['text'].encode('utf-8')).decode('ascii')) for item in selection['files']]
                result = attach(dict(conversation_id=identity[0], revision=identity[1],
                                     action='attach', name=selection['name'], files=files))
                self.clear()
                return result
        except ProjectLocationError as error:
            raise chat_error(error.code) from None
        return copy.deepcopy(self._result)
