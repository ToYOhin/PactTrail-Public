"""Explicitly started, ephemeral conversation on literal loopback."""
from __future__ import annotations

from importlib.resources import files
import secrets
from socketserver import TCPServer

from pacttrail.application.chat import chat_error, safe_chat_error
from pacttrail.domain.definitions import canonical_json
from pacttrail.platform.chat_http import ChatHandler


class ChatServer(TCPServer):
    allow_reuse_address = False
    # Hold a browser's six-way asset burst while the handler remains serial.
    request_queue_size = 8

    def __init__(self, session, port=0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise chat_error('invalid_request')
        self.session = session
        self._session_closed = False
        self.capability = secrets.token_hex(32)
        self.assets = {}
        try:
            catalog = files('pacttrail.platform').joinpath('chat_ui')
            for path, filename, media in (
                ('/', 'index.html', 'text/html; charset=utf-8'),
                ('/app.js', 'app.js', 'text/javascript; charset=utf-8'),
                ('/composer.js', 'composer.js', 'text/javascript; charset=utf-8'),
                ('/history_reference.js', 'history_reference.js', 'text/javascript; charset=utf-8'),
                ('/message_format.js', 'message_format.js', 'text/javascript; charset=utf-8'),
                ('/send_preview.js', 'send_preview.js', 'text/javascript; charset=utf-8'),
                ('/model_connection.js', 'model_connection.js', 'text/javascript; charset=utf-8'),
                ('/task.js', 'task.js', 'text/javascript; charset=utf-8'),
                ('/task_history.js', 'task_history.js', 'text/javascript; charset=utf-8'),
                ('/followup.js', 'followup.js', 'text/javascript; charset=utf-8'),
                ('/selection.js', 'selection.js', 'text/javascript; charset=utf-8'),
                ('/web.js', 'web.js', 'text/javascript; charset=utf-8'),
                ('/web_search.js', 'web_search.js', 'text/javascript; charset=utf-8'),
                ('/review.js', 'review.js', 'text/javascript; charset=utf-8'),
                ('/draft.js', 'draft.js', 'text/javascript; charset=utf-8'),
                ('/conversation.js', 'conversation.js', 'text/javascript; charset=utf-8'),
                ('/conversation_archive.js', 'conversation_archive.js', 'text/javascript; charset=utf-8'),
                ('/saved_input.js', 'saved_input.js', 'text/javascript; charset=utf-8'),
                ('/continuation.js', 'continuation.js', 'text/javascript; charset=utf-8'),
                ('/artifact_reference.js', 'artifact_reference.js', 'text/javascript; charset=utf-8'),
                ('/artifact_versions.js', 'artifact_versions.js', 'text/javascript; charset=utf-8'),
                ('/source_citations.js', 'source_citations.js', 'text/javascript; charset=utf-8'),
                ('/verification.js', 'verification.js', 'text/javascript; charset=utf-8'),
                ('/style.css', 'style.css', 'text/css; charset=utf-8'),
            ):
                with catalog.joinpath(filename).open('rb') as stream:
                    body = stream.read(65537)
                projected_length = len(body)
                if path == '/':
                    if body.count(b'__PACTTRAIL_SESSION__') != 1:
                        raise chat_error('state_unavailable')
                    projected_length += 64 - len(b'__PACTTRAIL_SESSION__')
                if projected_length > 65536:
                    raise chat_error('state_unavailable')
                self.assets[path] = (body, media)
            # TCPServer does not perform HTTPServer's hostname/DNS lookup.
            super().__init__(('127.0.0.1', port), ChatHandler)
        except Exception as error:
            self._close_session()
            raise safe_chat_error(error) from None
        self.host = f'127.0.0.1:{self.server_address[1]}'
        self.origin = f'http://{self.host}'

    def _close_session(self):
        if not self._session_closed:
            self._session_closed = True
            try:
                self.session.close()
            except Exception as error:
                raise safe_chat_error(error) from None

    def server_close(self):
        try:
            self._close_session()
        finally:
            super().server_close()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

    def handle_error(self, request, client_address):
        pass  # Default tracebacks might expose request content or capability.


def serve_chat(args, session):
    try:
        with ChatServer(session, args.port) as server:
            local_model = bool(getattr(args, 'local_model', False))
            tool_model = bool(getattr(args, 'agent_model', False))
            print(canonical_json({'url': server.origin + '/',
                                  'mode': ('task_history' if getattr(args, 'task_history_dir', None) is not None else
                                           'agent_model_config' if tool_model else 'local_model_config' if local_model else 'agent_demo'
                                           if (getattr(args, 'agent_demo', False) or
                                               getattr(args, 'agent_protocol_demo', False)) else 'offline_demo'),
                                  'offline': not (local_model or tool_model)}), flush=True)
            try:
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                pass
        return 0
    except Exception as error:
        raise safe_chat_error(error) from None
