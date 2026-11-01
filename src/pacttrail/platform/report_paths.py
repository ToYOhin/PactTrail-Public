from __future__ import annotations

import os
from pathlib import Path
import stat

from pacttrail.domain.evidence import MAX_OUTPUT_BYTES
from pacttrail.domain.evidence_report import file_identity, report_error


DB_LIMIT = 1048576
_NAMES = {'ledger.sqlite3', 'ledger.sqlite3-journal', 'report.pending', 'report.json'}


def plain(path: Path, *, directory=False, limit=MAX_OUTPUT_BYTES):
    metadata = path.lstat()
    if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not (stat.S_ISDIR if directory else stat.S_ISREG)(metadata.st_mode)):
        raise report_error('access_denied')
    if not directory and metadata.st_size > limit:
        raise report_error('unresolved')
    return metadata


def identity(metadata) -> tuple[int, int]:
    return file_identity((metadata.st_dev, metadata.st_ino))


def present(path: Path) -> bool:
    # exists() follows links and hides broken symlinks; lstat must remain visible.
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


class ReportPaths:
    def __init__(self, root: Path):
        raw = os.fspath(root)
        if (type(raw) is not str or not 1 <= len(raw) <= 1024
                or any(ord(c) < 32 or ord(c) == 127 for c in raw)
                or raw.startswith(('\\\\', '//'))):
            raise report_error('invalid_request')
        raw.encode('utf-8')
        self.root = Path(raw)
        if (not self.root.is_absolute() or '..' in self.root.parts
                or self.root == self.root.parent or len(self.root.parts) > 64
                or (os.name == 'nt' and any(':' in part or part.endswith(('.', ' '))
                                          for part in self.root.parts[1:]))):
            raise report_error('invalid_request')
        self.db = self.root / 'ledger.sqlite3'
        self.stage = self.root / 'report.pending'
        self.target = self.root / 'report.json'

    def check(self, *, create=False) -> tuple[int, int]:
        for parent in reversed(self.root.parents):
            plain(parent, directory=True)
        if create:
            self.root.mkdir()  # Exclusive reservation, never adopt an existing directory.
        root_id = identity(plain(self.root, directory=True))
        if self.root.resolve(strict=True) != self.root:
            raise report_error('access_denied')
        with os.scandir(self.root) as entries:
            for count, entry in enumerate(entries, 1):
                if count > len(_NAMES) or entry.name not in _NAMES:
                    raise report_error('unresolved')
                limit = DB_LIMIT if entry.name.startswith('ledger.') else MAX_OUTPUT_BYTES
                metadata = plain(self.root / entry.name, limit=limit)
                if entry.name.startswith('ledger.') and metadata.st_nlink != 1:
                    raise report_error('unresolved')
        return root_id

    def stage_bytes(self, recorded: list[int], *, published=False) -> bytes:
        stage = plain(self.stage)
        expected_links = 2 if published else 1
        if identity(stage) != tuple(recorded) or stage.st_nlink != expected_links:
            raise report_error('unresolved')
        if published:
            target = plain(self.target)
            if identity(target) != identity(stage) or target.st_nlink != 2:
                raise report_error('unresolved')
        with self.stage.open('rb') as stream:
            if identity(os.fstat(stream.fileno())) != identity(stage):
                raise report_error('unresolved')
            raw = stream.read(MAX_OUTPUT_BYTES + 1)
        if len(raw) > MAX_OUTPUT_BYTES:
            raise report_error('unresolved')
        return raw
