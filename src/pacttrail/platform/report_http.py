"""One bounded loopback transport; ReportService remains the write authority."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler
import io
from pathlib import Path
import re
import time
import unicodedata

from pacttrail.domain.definitions import canonical_json
from pacttrail.domain.evidence_report import parse_json, report_error, safe_report_error, token
from pacttrail.domain.runs import PactTrailError
from pacttrail.platform.report_paths import ReportPaths, plain, present


def _recovery_command(root, operator, run_id, digest):
    """Optional, literal PowerShell representation; never inspect or execute state."""
    if (type(root) is not str or not 1 <= len(root) <= 1024
            or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs', 'Zl', 'Zp'} for c in root)):
        return None
    try:
        ReportPaths(Path(root))  # Existing lexical root checks, without filesystem I/O.
        token(operator, r'[a-z][a-z0-9-]{0,31}')
        token(run_id, r'[a-f0-9]{32}')
        token(digest, r'[a-f0-9]{64}')
    except (PactTrailError, ValueError, TypeError):
        return None

    def literal(value):
        # PowerShell treats all four curly single quotes as delimiters too.
        return "'" + ''.join(c * 2 if c in "'\u2018\u2019\u201a\u201b" else c for c in value) + "'"

    parts = ['pacttrail evidence report serve']
    for name, value in (('state-dir', root), ('operator', operator),
                        ('run-id', run_id), ('proposal-sha256', digest)):
        parts.extend(('--' + name, literal(value)))
    return ' '.join(parts)


class ReportSession:
    def __init__(self, service, root: Path, operator: str, run_id=None, proposal_sha256=None):
        self.service, self.root, self.operator = service, ReportPaths(root).root, operator
        token(operator, r'[a-z][a-z0-9-]{0,31}')
        self.identity = None
        self.review_attempted = False
        if (run_id is None) != (proposal_sha256 is None):
            raise report_error('invalid_request')
        if run_id is not None:
            service.status(run_id, operator, proposal_sha256)
            self.identity = (run_id, operator, proposal_sha256)
            self.review_attempted = True
        else:
            for parent in reversed(self.root.parents):
                plain(parent, directory=True)
            if present(self.root):
                raise report_error('state_unavailable')

    def project(self, record=None):
        projected = None
        recovery_command = None
        if record is not None:
            proposal = record['proposal']
            projected = {key: proposal[key] for key in (
                'run_id', 'report_utf8', 'report_sha256', 'report_bytes', 'created_at', 'expires_at')}
            projected.update(proposal_sha256=record['proposal_sha256'],
                             query=proposal['arguments']['query'],
                             proposal_json=canonical_json(proposal))
            if (record['state'] in ('reviewed', 'approved', 'denied', 'intent', 'staging', 'complete')
                    and self.identity is not None
                    and (proposal['run_id'], proposal['operator'], record['proposal_sha256']) == self.identity
                    and proposal['operator'] == self.operator and proposal['root'] == str(self.root)):
                recovery_command = _recovery_command(str(self.root), self.operator,
                                                     self.identity[0], self.identity[2])
        return {'state': record['state'] if record else ('unresolved' if self.review_attempted else 'new'),
                'root': str(self.root), 'target': str(self.root / 'report.json'),
                'operator': self.operator, 'server_time': int(time.time()), 'record': projected,
                'recovery_command': recovery_command}

    def status(self):
        try:
            return self.project(self.service.status(*self.identity) if self.identity else None)
        except Exception as error:
            raise safe_report_error(error) from None

    def action(self, path, body):
        try:
            return self._action(path, body)
        except Exception as error:
            raise safe_report_error(error) from None

    def _action(self, path, body):
        if type(body) is not dict:
            raise report_error('invalid_request')
        if path == '/review':
            if set(body) != {'query'} or type(body['query']) is not str:
                raise report_error('invalid_request')
            if self.review_attempted:
                raise report_error('unresolved')
            # An error can follow a reserved root; never silently start a second operation.
            self.review_attempted = True
            record = self.service.review(body['query'], self.operator)
            self.identity = (record['proposal']['run_id'], self.operator, record['proposal_sha256'])
        else:
            if path not in ('/approve', '/deny', '/publish', '/resume') or set(body) != {'proposal_sha256'}:
                raise report_error('invalid_request')
            digest = token(body['proposal_sha256'], r'[a-f0-9]{64}')
            if self.identity is None or not hmac.compare_digest(digest, self.identity[2]):
                raise report_error('access_denied')
            if path in ('/approve', '/deny'):
                record = self.service.decide(*self.identity, path[1:])
            else:
                record = self.service.publish(*self.identity)
        return self.project(record)


class ReportHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'PactTrail'
    sys_version = ''
    _paths = {'/', '/app.js', '/style.css', '/status', '/review', '/approve', '/deny', '/publish', '/resume'}

    def log_message(self, *args):
        pass  # Neither rejected requests nor session capabilities belong in logs.

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {'error': 'invalid_request'})

    def respond(self, code, value, content_type='application/json; charset=utf-8'):
        body = value if type(value) is bytes else canonical_json(value).encode('utf-8')
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
            return  # A lost response never triggers another service operation.
        except Exception as error:
            code = str(safe_report_error(error, 'invalid_request')).split(': ')[-1]
            try:
                self.respond(403 if code == 'access_denied' else 400, {'error': code})
            except OSError:
                pass
        finally:
            self.close_connection = True

    def read_request(self):
        self.raw_requestline = self.rfile.readline(2049)
        if not self.raw_requestline:
            return
        if len(self.raw_requestline) > 2048 or not self.raw_requestline.endswith(b'\r\n'):
            raise report_error('invalid_request')
        parts = self.raw_requestline[:-2].split(b' ')
        if len(parts) != 3 or parts[2] not in (b'HTTP/1.0', b'HTTP/1.1'):
            raise report_error('invalid_request')
        if parts[1] not in {path.encode('ascii') for path in self._paths}:
            raise report_error('invalid_request')
        headers = bytearray()
        names = set()
        while True:
            line = self.rfile.readline(min(2049, 8193 - len(headers)))
            headers.extend(line)
            if len(headers) > 8192 or len(line) > 2048 or not line.endswith(b'\r\n'):
                raise report_error('invalid_request')
            if line == b'\r\n':
                break
            key, colon, value = line[:-2].partition(b':')
            if (not colon or not re.fullmatch(rb'[!#$%&\'*+.^_`|~0-9A-Za-z-]+', key)
                    or any(c < 32 or c == 127 for c in value) or key.lower() in names or len(names) >= 32):
                raise report_error('invalid_request')
            names.add(key.lower())
        if names & {b'transfer-encoding', b'expect'}:
            raise report_error('invalid_request')
        stream = self.rfile
        try:
            self.rfile = io.BytesIO(headers)
            if not self.parse_request():
                return
        finally:
            self.rfile = stream
        self.close_connection = True
        if self.command not in ('GET', 'POST'):
            self.respond(405, {'error': 'invalid_request'})
            return
        self.dispatch()

    def dispatch(self):
        header = self.headers.get
        if header('Host') != self.server.host:
            raise report_error('access_denied')
        origin = header('Origin')
        if origin is not None and origin != self.server.origin:
            raise report_error('access_denied')
        site, mode, dest = header('Sec-Fetch-Site'), header('Sec-Fetch-Mode'), header('Sec-Fetch-Dest')
        if self.command == 'GET' and self.path == '/':
            allowed = site in ('none', 'same-origin') and mode == 'navigate' and dest == 'document'
        elif self.command == 'GET' and self.path in ('/app.js', '/style.css'):
            allowed = site == 'same-origin' and mode in ('no-cors', 'same-origin') and dest == (
                'script' if self.path == '/app.js' else 'style')
        else:
            capability = header('X-PactTrail-Session', '')
            allowed = (site == 'same-origin' and mode == 'same-origin' and dest == 'empty'
                       and re.fullmatch(r'[a-f0-9]{64}', capability) is not None
                       and hmac.compare_digest(capability, self.server.capability))
            if self.command == 'POST':
                allowed = allowed and origin == self.server.origin
        if not allowed:
            raise report_error('access_denied')
        length = header('Content-Length', '0')
        if not re.fullmatch(r'[0-9]{1,4}', length) or int(length) > 2048:
            raise report_error('invalid_request')
        if self.command == 'GET':
            if int(length) != 0:
                raise report_error('invalid_request')
            if self.path == '/status':
                self.respond(200, self.server.session.status())
            elif self.path in self.server.assets:
                body, media = self.server.assets[self.path]
                if self.path == '/':
                    body = body.replace(b'__PACTTRAIL_SESSION__', self.server.capability.encode('ascii'))
                self.respond(200, body, media)
            else:
                self.respond(405, {'error': 'invalid_request'})
        else:
            if self.path not in ('/review', '/approve', '/deny', '/publish', '/resume'):
                self.respond(405, {'error': 'invalid_request'})
                return
            if header('Content-Type') != 'application/json' or int(length) == 0:
                raise report_error('invalid_request')
            raw = self.rfile.read(int(length))
            if len(raw) != int(length):
                raise report_error('invalid_request')
            try:
                body = parse_json(raw.decode('utf-8'))
            except Exception:
                raise report_error('invalid_request') from None
            self.respond(200, self.server.session.action(self.path, body))
