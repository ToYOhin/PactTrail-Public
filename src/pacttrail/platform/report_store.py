from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time
import uuid

from pacttrail.domain.definitions import canonical_json
from pacttrail.domain.evidence_report import (
    RECORD_LIMIT, SOURCE_HASHES, ReportProposal, report_error, safe_report_error,
)
from pacttrail.domain.report_record import (
    check_time, new_record, read_record, transition, validate_identity,
)
from pacttrail.platform.report_paths import ReportPaths, identity, present


_TABLE = 'CREATE TABLE operation (id INTEGER PRIMARY KEY CHECK (id = 1), record TEXT NOT NULL)'
_APP_ID = 1347703344


class LocalReportStore:
    """One trusted local operation, with explicit commits before report bytes."""

    def __init__(self, root: Path):
        self.paths = ReportPaths(root)

    @contextmanager
    def _connection(self):
        self.paths.check()
        connection = sqlite3.connect(self.paths.db.as_uri() + '?mode=rw', uri=True,
                                     timeout=0.25, isolation_level=None)
        try:
            connection.execute('PRAGMA trusted_schema=OFF')
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('PRAGMA max_page_count=256')
            if connection.execute('PRAGMA journal_mode').fetchone() != ('delete',):
                raise report_error('unresolved')
            yield connection
        finally:
            connection.close()

    def _read(self, connection) -> dict:
        if (connection.execute('PRAGMA application_id').fetchone() != (_APP_ID,)
                or connection.execute('PRAGMA user_version').fetchone() != (1,)
                or connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]
                or connection.execute('SELECT type, name, sql FROM sqlite_master').fetchall()
                != [('table', 'operation', _TABLE)]):
            raise report_error('unresolved')
        sizes = connection.execute('SELECT id, length(CAST(record AS BLOB)) FROM operation LIMIT 2').fetchall()
        if len(sizes) != 1 or sizes[0][0] != 1 or not 0 < sizes[0][1] <= RECORD_LIMIT:
            raise report_error('unresolved')
        record = read_record(connection.execute('SELECT record FROM operation WHERE id=1').fetchone()[0])
        if (record['proposal']['root'] != str(self.paths.root)
                or tuple(record['proposal']['root_identity']) != self.paths.check()):
            raise report_error('unresolved')
        return record

    def _save(self, record: dict, previous: str):
        encoded = canonical_json(record)
        read_record(encoded)
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = self._read(connection)
            if (current['state'] != previous
                    or current['proposal_sha256'] != record['proposal_sha256']):
                raise report_error('unresolved')
            connection.execute('UPDATE operation SET record=? WHERE id=1', (encoded,))
            connection.execute('COMMIT')

    def review(self, query: str, operator: str, report: str, now: int) -> dict:
        try:
            # Validate before creating control state as well as in the record reader.
            ReportProposal('0' * 32, operator, str(self.paths.root), (1, 1), query, report, now)
            root_id = self.paths.check(create=True)
            record = new_record(ReportProposal(uuid.uuid4().hex, operator, str(self.paths.root),
                                               root_id, query, report, now))
            with self.paths.db.open('xb'):
                pass
            with self._connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute(_TABLE)
                connection.execute(f'PRAGMA application_id={_APP_ID}')
                connection.execute('PRAGMA user_version=1')
                connection.execute('INSERT INTO operation VALUES (1, ?)', (canonical_json(record),))
                connection.execute('COMMIT')
            return record
        except Exception as error:
            raise safe_report_error(error) from None

    def load(self, run_id: str, operator: str, proposal_sha256: str) -> dict:
        try:
            with self._connection() as connection:
                record = self._read(connection)
            validate_identity(record, run_id, operator, proposal_sha256)
            if record['state'] == 'complete':
                self._complete(record, record['audit'][-1]['at'])
            return record
        except Exception as error:
            raise safe_report_error(error) from None

    def decide(self, run_id: str, operator: str, proposal_sha256: str, decision: str, now: int) -> dict:
        try:
            record = self.load(run_id, operator, proposal_sha256)
            if decision not in ('approve', 'deny') or type(decision) is not str:
                raise report_error('invalid_request')
            desired = 'approved' if decision == 'approve' else 'denied'
            if record['state'] != 'reviewed':
                if record['audit'][1]['state'] == desired:
                    return record
                raise report_error('access_denied')
            check_time(record, now)
            transition(record, desired, now)
            self._save(record, 'reviewed')
            return record
        except Exception as error:
            raise safe_report_error(error) from None

    def _stage(self, record, now):
        if record['state'] == 'intent':
            if present(self.paths.stage):
                raise report_error('unresolved')
            now = self._before_write(record)
            with self.paths.stage.open('xb') as stream:
                stage_id = identity(os.fstat(stream.fileno()))
            record['stage_identity'] = list(stage_id)
            transition(record, 'staging', now)
            self._save(record, 'intent')
        approved = record['proposal']['report_utf8'].encode('utf-8')
        existing = self.paths.stage_bytes(record['stage_identity'])
        if not approved.startswith(existing):
            raise report_error('unresolved')
        if len(existing) != len(approved):
            self._before_write(record)
            self._append(record, existing, approved)
        # fsync even a complete retained stage before publishing it after restart.
        with self.paths.stage.open('r+b') as stream:
            if identity(os.fstat(stream.fileno())) != tuple(record['stage_identity']):
                raise report_error('unresolved')
            os.fsync(stream.fileno())
        if self.paths.stage_bytes(record['stage_identity']) != approved:
            raise report_error('unresolved')

    def _append(self, record, existing: bytes, approved: bytes):
        with self.paths.stage.open('ab') as stream:
            metadata = os.fstat(stream.fileno())
            if (identity(metadata) != tuple(record['stage_identity'])
                    or metadata.st_size != len(existing)):
                raise report_error('unresolved')
            remaining = approved[len(existing):]
            if stream.write(remaining) != len(remaining):
                raise report_error('unresolved')
            stream.flush()
            os.fsync(stream.fileno())

    def _before_write(self, record):
        self.paths.check()
        now = int(time.time())
        check_time(record, now)
        return now

    def _complete(self, record, now):
        expected = record['proposal']['report_utf8'].encode('utf-8')
        if self.paths.stage_bytes(record['stage_identity'], published=True) != expected:
            raise report_error('unresolved')
        if record['state'] != 'complete':
            check_time(record, now, unfinished=False)
            transition(record, 'complete', now)
            self._save(record, 'staging')
        return record

    def publish(self, run_id: str, operator: str, proposal_sha256: str,
                now: int, sources: tuple[str, ...] | None) -> dict:
        try:
            record = self.load(run_id, operator, proposal_sha256)
            state = record['state']
            if state == 'reviewed':
                raise report_error('approval_required')
            if state == 'denied':
                raise report_error('denied')
            if present(self.paths.target):
                if state not in ('staging', 'complete'):
                    raise report_error('unresolved')
                check_time(record, now, unfinished=False)
                return self._complete(record, now)
            if state == 'complete':
                raise report_error('unresolved')
            check_time(record, now)
            if type(sources) is not tuple or sources != SOURCE_HASHES:
                raise report_error('source_changed')
            if state == 'approved':
                if present(self.paths.stage):
                    raise report_error('unresolved')
                transition(record, 'intent', now)
                self._save(record, 'approved')  # Consumption and intent committed together.
            self._stage(record, now)
            self._before_write(record)
            os.link(self.paths.stage, self.paths.target)  # Exclusive: no replace/copy fallback.
            return self._complete(record, int(time.time()))
        except Exception as error:
            raise safe_report_error(error, 'unresolved') from None
