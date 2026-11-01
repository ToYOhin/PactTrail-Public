"""CLI composition for an explicitly started single-operator loopback session."""
from __future__ import annotations

from importlib.resources import files
import secrets
from socketserver import TCPServer

from pacttrail.domain.definitions import canonical_json
from pacttrail.domain.evidence_report import report_error, safe_report_error
from pacttrail.platform.report_http import ReportHandler, ReportSession


class ReportServer(TCPServer):
    allow_reuse_address = False
    request_queue_size = 4

    def __init__(self, session, port=0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise report_error('invalid_request')
        self.session = session
        self.capability = secrets.token_hex(32)
        self.assets = {}
        catalog = files('pacttrail.platform').joinpath('report_ui')
        for path, filename, media in (
            ('/', 'index.html', 'text/html; charset=utf-8'),
            ('/app.js', 'app.js', 'text/javascript; charset=utf-8'),
            ('/style.css', 'style.css', 'text/css; charset=utf-8'),
        ):
            with catalog.joinpath(filename).open('rb') as stream:
                body = stream.read(131073)
            if len(body) > 131072 or (path == '/' and body.count(b'__PACTTRAIL_SESSION__') != 1):
                raise report_error('state_unavailable')
            self.assets[path] = (body, media)
        # TCPServer avoids HTTPServer's hostname/DNS lookup; only the literal loopback binds.
        super().__init__(('127.0.0.1', port), ReportHandler)
        self.host = f'127.0.0.1:{self.server_address[1]}'
        self.origin = f'http://{self.host}'

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

    def handle_error(self, request, client_address):
        pass  # Default tracebacks could disclose request or local state content.


def serve_report(args, service, root):
    try:
        session = ReportSession(service, root, args.operator, args.run_id, args.proposal_sha256)
        with ReportServer(session, args.port) as server:
            print(canonical_json({'url': server.origin + '/', 'mode': 'local-synthetic-report'}), flush=True)
            try:
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                pass
        return 0
    except Exception as error:
        raise safe_report_error(error) from None
