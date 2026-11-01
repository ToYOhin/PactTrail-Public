from __future__ import annotations

import json
import os
from pathlib import Path

from pacttrail.domain.definitions import canonical_json
from pacttrail.domain.run_bindings import RunBinding
from pacttrail.domain.runs import PactTrailError, validate_run_id


MAX_RUN_BINDING_BYTES = 1024 * 1024
MAX_RUN_BINDING_JSON_DEPTH = 64


class FileRunBindingStore:
    """Product-owned, fail-closed sidecar for exact Run-to-Flow bindings."""

    def __init__(self, data_dir: str | Path):
        self.data_directory = Path(data_dir).resolve()
        self.directory = self.data_directory / "run-bindings"

    def has_claim(self, run_id: str) -> bool:
        try:
            return self._run_directory(run_id).exists()
        except OSError as exc:
            raise PactTrailError(
                f"invalid_run_binding: cannot inspect run {run_id}: "
                f"{type(exc).__name__}"
            ) from exc

    def create(self, binding: RunBinding) -> Path:
        try:
            validated_binding = RunBinding.from_dict(binding.to_dict())
            serialized = canonical_json(validated_binding.to_dict()) + "\n"
        except (PactTrailError, RecursionError) as exc:
            raise PactTrailError(
                f"invalid_run_binding: cannot publish invalid binding: {exc}"
            ) from exc
        if len(serialized.encode("utf-8")) > MAX_RUN_BINDING_BYTES:
            raise PactTrailError(
                f"invalid_run_binding: binding exceeds {MAX_RUN_BINDING_BYTES} bytes"
            )

        binding_root = self._binding_root()
        try:
            binding_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PactTrailError(
                f"invalid_run_binding: cannot prepare binding store for "
                f"{validated_binding.run_id}: {type(exc).__name__}"
            ) from exc
        binding_root = self._binding_root()
        run_directory = binding_root / validated_binding.run_id
        try:
            run_directory.mkdir()
        except FileExistsError as exc:
            raise PactTrailError(
                "run_binding_exists: run is already bound or reserved: "
                f"{validated_binding.run_id}"
            ) from exc
        except OSError as exc:
            raise PactTrailError(
                f"invalid_run_binding: cannot reserve run {validated_binding.run_id}: "
                f"{type(exc).__name__}"
            ) from exc

        binding_path = run_directory / "binding.json"
        temporary_path = run_directory / ".binding.tmp"
        try:
            with temporary_path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, binding_path)
        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise PactTrailError(
                f"invalid_run_binding: cannot persist run {validated_binding.run_id}: "
                f"{type(exc).__name__}"
            ) from exc
        return binding_path

    def load(self, run_id: str) -> RunBinding | None:
        selected_run_id = validate_run_id(run_id)
        run_directory = self._run_directory(selected_run_id)
        try:
            if not run_directory.exists():
                return None
        except OSError as exc:
            raise PactTrailError(
                f"invalid_run_binding: cannot inspect run {selected_run_id}: "
                f"{type(exc).__name__}"
            ) from exc
        try:
            resolved_run_directory = run_directory.resolve()
            resolved_binding_root = self._binding_root()
        except (OSError, RuntimeError) as exc:
            raise PactTrailError(
                f"invalid_run_binding: cannot resolve run {selected_run_id}: "
                f"{type(exc).__name__}"
            ) from exc
        if resolved_run_directory != resolved_binding_root / selected_run_id:
            raise PactTrailError(
                f"invalid_run_binding: binding directory escapes run {selected_run_id}"
            )
        binding_path = run_directory / "binding.json"
        if not run_directory.is_dir() or not binding_path.is_file():
            raise PactTrailError(
                f"invalid_run_binding: incomplete binding for run {selected_run_id}"
            )
        try:
            resolved_binding_path = binding_path.resolve()
        except (OSError, RuntimeError) as exc:
            raise PactTrailError(
                f"invalid_run_binding: cannot resolve binding for run "
                f"{selected_run_id}: {type(exc).__name__}"
            ) from exc
        if resolved_binding_path != resolved_run_directory / "binding.json":
            raise PactTrailError(
                f"invalid_run_binding: binding path escapes run {selected_run_id}"
            )
        try:
            with resolved_binding_path.open("rb") as stream:
                binding_bytes = stream.read(MAX_RUN_BINDING_BYTES + 1)
            if len(binding_bytes) > MAX_RUN_BINDING_BYTES:
                raise PactTrailError(
                    f"binding exceeds {MAX_RUN_BINDING_BYTES} bytes"
                )
            payload = json.loads(
                binding_bytes.decode("utf-8"),
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_nonstandard_json_constant,
            )
            _validate_json_depth(payload)
            binding = RunBinding.from_dict(payload)
        except (
            OSError,
            UnicodeError,
            ValueError,
            RecursionError,
            PactTrailError,
        ) as exc:
            raise PactTrailError(
                f"invalid_run_binding: run {selected_run_id}: {exc}"
            ) from exc
        if binding.run_id != selected_run_id:
            raise PactTrailError(
                f"invalid_run_binding: run id mismatch for {selected_run_id}"
            )
        return binding

    def _run_directory(self, run_id: str) -> Path:
        return self._binding_root() / validate_run_id(run_id)

    def _binding_root(self) -> Path:
        expected_root = self.data_directory / "run-bindings"
        try:
            resolved_root = self.directory.resolve()
        except (OSError, RuntimeError) as exc:
            raise PactTrailError(
                "invalid_run_binding: cannot resolve binding store: "
                f"{type(exc).__name__}"
            ) from exc
        if resolved_root != expected_root:
            raise PactTrailError(
                "invalid_run_binding: binding store escapes data directory"
            )
        return resolved_root


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PactTrailError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> object:
    raise PactTrailError(f"non-standard JSON constant: {value}")


def _validate_json_depth(value: object) -> None:
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_RUN_BINDING_JSON_DEPTH:
            raise PactTrailError(
                f"JSON nesting exceeds {MAX_RUN_BINDING_JSON_DEPTH} levels"
            )
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
