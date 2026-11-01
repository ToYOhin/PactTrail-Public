from __future__ import annotations

from pathlib import Path
import stat

from pacttrail.domain.evidence import (
    MAX_FILE_BYTES,
    SAMPLE_FILES,
    evidence_error,
    safe_evidence_error,
)


_SAMPLE_ROOT = Path(__file__).parent / "evidence_samples"


def _plain_metadata(path: Path, *, directory: bool) -> None:
    metadata = path.lstat()
    if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise evidence_error("access_denied")
    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_kind(metadata.st_mode):
        raise evidence_error("invalid_source")
    if not directory and metadata.st_size > MAX_FILE_BYTES:
        raise evidence_error("input_limit")


class SampleEvidenceReader:
    """Read only the three packaged samples under trusted local ownership."""

    __slots__ = ()

    def read(self, file_id: str) -> str:
        if type(file_id) is not str or file_id not in SAMPLE_FILES:
            raise evidence_error("access_denied")
        try:
            # Inspect original paths before resolve: a redirected root is not
            # made trustworthy by accepting the destination as the new root.
            _plain_metadata(_SAMPLE_ROOT, directory=True)
            candidate = _SAMPLE_ROOT / file_id
            _plain_metadata(candidate, directory=False)
            root = _SAMPLE_ROOT.resolve(strict=True)
            resolved = candidate.resolve(strict=True)
            if resolved.parent != root or resolved.name != file_id:
                raise evidence_error("access_denied")
            with candidate.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise evidence_error("input_limit")
            try:
                return raw.decode("utf-8")
            except UnicodeError:
                raise evidence_error("invalid_source") from None
        except Exception as error:
            raise safe_evidence_error(error, "source_unavailable") from None
