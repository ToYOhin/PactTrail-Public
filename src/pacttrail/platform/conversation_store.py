"""One bounded local conversation document; no legacy-store migration."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import threading

from pacttrail.application.chat import chat_error
from pacttrail.domain.runs import PactTrailError


DOCUMENT_LIMIT = 2 * 1024 * 1024
DATABASE_LIMIT = 4 * 1024 * 1024
_NAME = 'conversations.sqlite3'
_APP_ID = 1347703347
_TABLE = 'CREATE TABLE workspace (id INTEGER PRIMARY KEY CHECK (id = 1), document TEXT NOT NULL)'
_CODES = {'history_unavailable', 'history_in_use', 'history_version', 'history_corrupt'}


def _failure(error):
    if (type(error) is PactTrailError and len(error.args) == 1 and type(error.args[0]) is str
            and error.args[0] in {'chat_error: ' + code for code in _CODES}):
        return error
    code = getattr(error, 'sqlite_errorcode', None)
    if code is not None and code & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return chat_error('history_in_use')
    if code is not None and code & 255 in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        return chat_error('history_corrupt')
    # Python 3.10 does not expose sqlite_errorcode. Match only SQLite's fixed
    # messages, never propagate them or a path-bearing exception to the page.
    if code is None and isinstance(error, sqlite3.Error):
        if str(error) in ('database is locked', 'database table is locked'):
            return chat_error('history_in_use')
        if str(error) in ('file is not a database', 'database disk image is malformed'):
            return chat_error('history_corrupt')
    return chat_error('history_unavailable')


def _plain(path, *, directory=False, limit=DATABASE_LIMIT):
    metadata = path.lstat()
    if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR if directory else stat.S_ISREG)(metadata.st_mode)
            or (not directory and metadata.st_nlink != 1)):
        raise chat_error('history_unavailable')
    if not directory and metadata.st_size > limit:
        raise chat_error('history_corrupt')
    return metadata.st_dev, metadata.st_ino


def _check_json(value, *, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if depth > 32 or count[0] > 200000:
        raise ValueError('bounded document')
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError('string keys required')
            _check_json(key, depth=depth + 1, count=count)
            _check_json(item, depth=depth + 1, count=count)
    elif type(value) is list:
        for item in value:
            _check_json(item, depth=depth + 1, count=count)
    elif type(value) is str:
        if len(value) > DOCUMENT_LIMIT:
            raise ValueError('bounded string')
        value.encode('utf-8')
    elif type(value) is float:
        if not math.isfinite(value):
            raise ValueError('finite numbers required')
    elif type(value) is int:
        if value.bit_length() > 256:
            raise ValueError('bounded integer')
    elif value is not None and type(value) is not bool:
        raise ValueError('JSON value required')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _constant(_):
    raise ValueError('finite numbers required')


def _decode(document):
    try:
        if type(document) is not str or not 0 < len(document.encode('utf-8')) <= DOCUMENT_LIMIT:
            raise ValueError('bounded text document required')
        value = json.loads(document, object_pairs_hook=_pairs, parse_constant=_constant)
        if type(value) is not dict:
            raise ValueError('object required')
        _check_json(value)
        return value
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise chat_error('history_corrupt') from None


def _encode(document):
    try:
        if type(document) is not dict:
            raise ValueError('object required')
        _check_json(document)
        chunks, size = [], 0
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        for chunk in encoder.iterencode(document):
            size += len(chunk.encode('utf-8'))
            if size > DOCUMENT_LIMIT:
                raise ValueError('bounded document')
            chunks.append(chunk)
        return ''.join(chunks)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise chat_error('history_corrupt') from None


class ConversationStore:
    """Single-server SQLite ownership with atomic replacement of one JSON object.

    The application validates the conversation schema; this adapter bounds and
    preserves its document. Local path checks do not sandbox a hostile OS user.
    """

    def __init__(self, directory):
        self._connection = None
        self._lock = threading.RLock()
        self._root_identity = self._db_identity = None
        try:
            raw = os.fspath(directory)
            if (type(raw) is not str or not 1 <= len(raw) <= 4096
                    or any(ord(c) < 32 or ord(c) == 127 for c in raw)
                    or raw.startswith(('\\\\', '//'))):
                raise chat_error('history_unavailable')
            raw.encode('utf-8')
            root = Path(raw)
            if ('..' in root.parts or len(root.parts) > 64
                    or (os.name == 'nt' and any(':' in part or part.endswith(('.', ' '))
                                              for part in root.parts[1:]))):
                raise chat_error('history_unavailable')
            self._root = root.absolute()
            if self._root == self._root.parent:
                raise chat_error('history_unavailable')
            self._db = self._root / _NAME
            self.location = str(self._db)
            for parent in reversed(self._root.parents):
                try:
                    _plain(parent, directory=True)
                except FileNotFoundError:
                    break
            self._root.mkdir(parents=True, exist_ok=True)
            self._check_paths()
            self._root_identity = _plain(self._root, directory=True)
            try:
                self._db.lstat()
                created = False
            except FileNotFoundError:
                # Reserve the fixed name exclusively; never replace an existing file.
                descriptor = os.open(self._db, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                os.close(descriptor)
                created = True
            self._db_identity = _plain(self._db)
            if not created and self._db.stat().st_size == 0:
                raise chat_error('history_corrupt')
            connection = sqlite3.connect(self._db.as_uri() + '?mode=rw', uri=True,
                                         timeout=0, isolation_level=None, check_same_thread=False)
            self._connection = connection
            connection.execute('PRAGMA trusted_schema=OFF')
            if connection.execute('PRAGMA journal_mode').fetchone() != ('delete',):
                raise chat_error('history_corrupt')
            connection.execute('PRAGMA locking_mode=EXCLUSIVE')
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN EXCLUSIVE')
            if created:
                connection.execute(_TABLE)
                connection.execute(f'PRAGMA application_id={_APP_ID}')
                connection.execute('PRAGMA user_version=1')
            self._validate()
            page_size = connection.execute('PRAGMA page_size').fetchone()[0]
            connection.execute(f'PRAGMA max_page_count={DATABASE_LIMIT // page_size}')
            connection.commit()
            # EXCLUSIVE locking retains ownership between these committed saves.
            self._check_paths()
        except Exception as error:
            self.close()
            raise _failure(error) from None

    def _check_paths(self):
        for parent in reversed(self._root.parents):
            _plain(parent, directory=True)
        root_identity = _plain(self._root, directory=True)
        if self._root_identity is not None and root_identity != self._root_identity:
            raise chat_error('history_unavailable')
        names = {_NAME, _NAME + '-journal'}
        with os.scandir(self._root) as entries:
            for number, entry in enumerate(entries, 1):
                if number > 2 or entry.name not in names:
                    raise chat_error('history_corrupt')
                limit = DATABASE_LIMIT if entry.name == _NAME else DATABASE_LIMIT + 65536
                identity = _plain(self._root / entry.name, limit=limit)
                if (entry.name == _NAME and self._db_identity is not None
                        and identity != self._db_identity):
                    raise chat_error('history_unavailable')
        if self._db_identity is not None and _plain(self._db) != self._db_identity:
            raise chat_error('history_unavailable')

    def _validate(self):
        connection = self._connection
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version != 1:
            raise chat_error('history_version' if version else 'history_corrupt')
        if (connection.execute('PRAGMA application_id').fetchone() != (_APP_ID,)
                or connection.execute('SELECT type, name, sql FROM sqlite_master').fetchall()
                != [('table', 'workspace', _TABLE)]
                or connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]):
            raise chat_error('history_corrupt')
        rows = connection.execute(
            'SELECT id, typeof(document), length(CAST(document AS BLOB)) FROM workspace LIMIT 2'
        ).fetchall()
        if len(rows) > 1 or (rows and (rows[0][0:2] != (1, 'text')
                                      or not 0 < rows[0][2] <= DOCUMENT_LIMIT)):
            raise chat_error('history_corrupt')
        if rows:
            return _decode(connection.execute('SELECT document FROM workspace WHERE id=1').fetchone()[0])
        return None

    def load(self) -> dict | None:
        with self._lock:
            try:
                if self._connection is None:
                    raise chat_error('history_unavailable')
                self._check_paths()
                return self._validate()
            except Exception as error:
                raise _failure(error) from None

    def save(self, document: dict):
        with self._lock:
            try:
                if self._connection is None:
                    raise chat_error('history_unavailable')
                encoded = _encode(document)
                self._check_paths()
                self._connection.execute('BEGIN IMMEDIATE')
                self._connection.execute(
                    'INSERT INTO workspace (id, document) VALUES (1, ?) '
                    'ON CONFLICT(id) DO UPDATE SET document=excluded.document', (encoded,)
                )
                self._connection.commit()
            except Exception as error:
                if self._connection is not None:
                    try:
                        self._connection.rollback()
                    except sqlite3.Error:
                        self.close()
                raise _failure(error) from None

    def close(self):
        with self._lock:
            connection, self._connection = self._connection, None
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
