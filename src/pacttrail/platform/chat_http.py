"""Closed loopback chat transport; the application owns ephemeral chat state."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler
import io
import json
import re

from pacttrail.application.chat import chat_error, safe_chat_error
from pacttrail.domain.definitions import canonical_json


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise chat_error('invalid_request')
        result[key] = value
    return result


def _invalid_constant(value):
    raise chat_error('invalid_request')


class ChatHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'PactTrail'
    sys_version = ''
    _paths = {'/', '/app.js', '/composer.js', '/history_reference.js', '/message_format.js', '/send_preview.js', '/model_connection.js', '/task.js', '/task_history.js', '/followup.js', '/selection.js', '/web.js', '/web_search.js', '/draft.js', '/review.js', '/conversation.js', '/conversation_archive.js', '/saved_input.js', '/continuation.js', '/artifact_reference.js', '/artifact_versions.js', '/source_citations.js', '/verification.js', '/style.css', '/status', '/send', '/preview', '/stop', '/reset', '/history', '/task-history', '/agent-history', '/continuation', '/artifact-reference', '/artifact-versions', '/source-evidence', '/followup', '/material', '/project', '/web', '/connection', '/draft-apply', '/draft-precheck', '/conversation', '/verify', '/project-locate'}

    def log_message(self, *args):
        pass  # The per-process capability and user content never belong in logs.

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {'error': 'chat_error: invalid_request'})

    def respond(self, code, value, content_type='application/json; charset=utf-8'):
        body = value if type(value) is bytes else canonical_json(value).encode('utf-8')
        maximum = 65536 if type(value) is bytes else getattr(self.server.session, 'response_byte_limit', 65536)
        if type(maximum) is not int or maximum not in (65536, 524288):
            maximum = 65536
        if len(body) > maximum:
            raise chat_error('response_limit')
        self.send_response_only(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def handle_one_request(self):
        self.close_connection = True
        self.request_version = 'HTTP/1.1'
        self.command = None
        self.requestline = ''
        try:
            self.read_request()
        except (TimeoutError, ConnectionError, OSError):
            return  # A lost response is reconciled through status, never an automatic send.
        except Exception as error:
            safe = str(safe_chat_error(error, 'invalid_request'))
            try:
                self.respond(403 if safe == 'chat_error: access_denied' else 400, {'error': safe})
            except OSError:
                pass
        finally:
            self.close_connection = True

    def read_request(self):
        # Keep this small parser aligned with the existing report transport without
        # coupling chat to report identity, durable state, or its approval lifecycle.
        self.raw_requestline = self.rfile.readline(2049)
        if not self.raw_requestline:
            return
        if len(self.raw_requestline) > 2048 or not self.raw_requestline.endswith(b'\r\n'):
            raise chat_error('invalid_request')
        parts = self.raw_requestline[:-2].split(b' ')
        if (len(parts) != 3 or parts[2] not in (b'HTTP/1.0', b'HTTP/1.1')
                or re.fullmatch(rb'[A-Z]+', parts[0]) is None):
            raise chat_error('invalid_request')
        if parts[1] not in {path.encode('ascii') for path in self._paths}:
            raise chat_error('invalid_request')
        headers = bytearray()
        names = set()
        while True:
            line = self.rfile.readline(min(2049, 8193 - len(headers)))
            headers.extend(line)
            if len(headers) > 8192 or len(line) > 2048 or not line.endswith(b'\r\n'):
                raise chat_error('invalid_request')
            if line == b'\r\n':
                break
            key, colon, value = line[:-2].partition(b':')
            if (not colon or not re.fullmatch(rb'[!#$%&\'*+.^_`|~0-9A-Za-z-]+', key)
                    or any(c < 32 or c == 127 for c in value) or key.lower() in names or len(names) >= 32):
                raise chat_error('invalid_request')
            names.add(key.lower())
        if names & {b'transfer-encoding', b'expect'}:
            raise chat_error('invalid_request')
        stream = self.rfile
        try:
            self.rfile = io.BytesIO(headers)
            if not self.parse_request():
                return
        finally:
            self.rfile = stream
        self.close_connection = True
        if self.command not in ('GET', 'POST'):
            self.respond(405, {'error': 'chat_error: invalid_request'})
            return
        self.dispatch()

    def dispatch(self):
        header = self.headers.get
        if header('Host') != self.server.host:
            raise chat_error('access_denied')
        origin = header('Origin')
        if origin is not None and origin != self.server.origin:
            raise chat_error('access_denied')
        site, mode, dest = header('Sec-Fetch-Site'), header('Sec-Fetch-Mode'), header('Sec-Fetch-Dest')
        if self.command == 'GET' and self.path == '/':
            allowed = site in ('none', 'same-origin') and mode == 'navigate' and dest == 'document'
        elif self.command == 'GET' and self.path in ('/app.js', '/composer.js', '/history_reference.js', '/message_format.js', '/send_preview.js', '/model_connection.js', '/task.js', '/task_history.js', '/followup.js', '/selection.js', '/web.js', '/web_search.js', '/draft.js', '/review.js', '/conversation.js', '/conversation_archive.js', '/saved_input.js', '/continuation.js', '/artifact_reference.js', '/artifact_versions.js', '/source_citations.js', '/verification.js', '/style.css'):
            allowed = site == 'same-origin' and mode in ('no-cors', 'same-origin') and dest == (
                'script' if self.path in ('/app.js', '/composer.js', '/history_reference.js', '/message_format.js', '/send_preview.js', '/model_connection.js', '/task.js', '/task_history.js', '/followup.js', '/selection.js', '/web.js', '/web_search.js', '/draft.js', '/review.js', '/conversation.js', '/conversation_archive.js', '/saved_input.js', '/continuation.js', '/artifact_reference.js', '/artifact_versions.js', '/source_citations.js', '/verification.js') else 'style')
        else:
            capability = header('X-PactTrail-Session', '')
            allowed = (site == 'same-origin' and mode == 'same-origin' and dest == 'empty'
                       and re.fullmatch(r'[a-f0-9]{64}', capability) is not None
                       and hmac.compare_digest(capability, self.server.capability))
            if self.command == 'POST':
                allowed = allowed and origin == self.server.origin
        if not allowed:
            raise chat_error('access_denied')
        length = header('Content-Length', '0')
        material_post = self.command == 'POST' and self.path in ('/material', '/project', '/project-locate')
        send_post = self.command == 'POST' and self.path == '/send'
        preview_post = self.command == 'POST' and self.path == '/preview'
        conversation_post = self.command == 'POST' and self.path == '/conversation'
        send_limit = 16384
        if preview_post or conversation_post:
            # Read-only preview cannot call status: an editing status can start
            # automatic syntax work. Business validation enforces launch limits.
            send_limit = 2048 + 12 * 4096
        if conversation_post:
            # Preserve the complete pending question and raw creation path,
            # including escaped non-BMP characters, without status preflight.
            send_limit = 53248
        if send_post:
            # A launch-configured Unicode goal may use twelve JSON bytes per
            # character (escaped surrogate pairs). Keep other routes unchanged.
            try:
                state = self.server.session.status() if callable(getattr(self.server.session, 'status', None)) else {}
            except Exception as error:
                raise safe_chat_error(error, 'invalid_request') from None
            if state.get('agent_history_available') is True:
                maximum = state.get('limits', {}).get('max_message_chars', 2048)
                if type(maximum) is int and 1 <= maximum <= 4096:
                    send_limit = max(send_limit, 2048 + 12 * max(2048, maximum))
        if (not re.fullmatch(r'[0-9]{1,5}' if material_post or send_post or preview_post or conversation_post else r'[0-9]{1,4}', length)
                or int(length) > (send_limit if send_post or preview_post or conversation_post else 16384 if self.path == '/project' else 12288 if material_post else 2048)):
            raise chat_error('invalid_request')
        if self.command == 'GET':
            if int(length) != 0:
                raise chat_error('invalid_request')
            if self.path == '/status':
                try:
                    projection = self.server.session.status()
                except Exception as error:
                    raise safe_chat_error(error, 'invalid_request') from None
                self.respond(200, projection)
            elif self.path in self.server.assets:
                body, media = self.server.assets[self.path]
                if self.path == '/':
                    body = body.replace(b'__PACTTRAIL_SESSION__', self.server.capability.encode('ascii'))
                self.respond(200, body, media)
            else:
                self.respond(405, {'error': 'chat_error: invalid_request'})
        else:
            if self.path not in ('/send', '/preview', '/stop', '/reset', '/history', '/task-history', '/agent-history', '/continuation', '/artifact-reference', '/artifact-versions', '/source-evidence', '/followup', '/material', '/project', '/web', '/connection', '/draft-apply', '/draft-precheck', '/conversation', '/verify', '/project-locate'):
                self.respond(405, {'error': 'chat_error: invalid_request'})
                return
            if header('Content-Type') != 'application/json' or int(length) == 0:
                raise chat_error('invalid_request')
            raw = self.rfile.read(int(length))
            if len(raw) != int(length):
                raise chat_error('invalid_request')
            try:
                body = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                                  parse_constant=_invalid_constant)
                if type(body) is not dict:
                    raise chat_error('invalid_request')
            except Exception:
                raise chat_error('invalid_request') from None
            try:
                projection = self.server.session.action(self.path, body)
            except Exception as error:
                raise safe_chat_error(error, 'invalid_request') from None
            self.respond(202 if self.path == '/send' else 200, projection)
