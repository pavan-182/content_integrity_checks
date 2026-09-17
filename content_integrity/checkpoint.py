"""Atomic files and resumable stage checkpoints for one output directory.

Contract:
- A checkpoint holds the cumulative pipeline state after the last completed stage, plus the
  fingerprint of everything that determines that state (code, inputs, dictionary, options,
  gateway identity). A later run on the same output directory resumes only when the fingerprint
  matches exactly; otherwise it is rejected with the differing keys, and the operator either
  restores the original inputs/configuration or discards the checkpoint explicitly.
- Every file is written to a temporary name in the same directory, fsynced, and renamed, so an
  interrupted write leaves the previous complete file. The state file is written before the
  index that references it, so the index always points at a complete state.
- Model responses are not stored here; the gateway's content-addressed disk cache already makes
  repeated calls free, including calls completed inside an interrupted stage.
- States are pickled. Only resume from checkpoints this pipeline wrote into an output directory
  you control; never accept a checkpoint directory from an untrusted source.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

CHECKPOINT_VERSION = 1
PACKAGE_ROOT = Path(__file__).resolve().parent


class IncompatibleCheckpointError(RuntimeError):
    """An existing checkpoint was created from different code, inputs, or configuration."""


def atomic_write_bytes(path: str | Path, data: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    _fsync_directory(target.parent)
    return target


class _HashingWriter:
    def __init__(self, handle: Any) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        self.size += len(data)
        return self.handle.write(data)


def _atomic_pickle(path: Path, state: Any) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False)
    try:
        with handle:
            writer = _HashingWriter(handle)
            pickle.dump(state, writer, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)
    return writer.digest.hexdigest(), writer.size


def atomic_write_json(path: str | Path, data: Any) -> Path:
    return atomic_write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # Directory fsync is unsupported on some platforms; the rename is still atomic.
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def code_sha256() -> str:
    """Hash of the package source and rule files, so edited code never reuses stale stages."""
    digest = hashlib.sha256()
    for path in sorted(PACKAGE_ROOT.rglob("*")):
        if path.suffix in {".py", ".yaml"} and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(PACKAGE_ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


class CheckpointStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.index_path = self.directory / "checkpoint.json"

    def exists(self) -> bool:
        return self.index_path.is_file()

    def load(self, fingerprint: dict[str, Any]) -> tuple[Any, dict[str, Any]] | None:
        """Return (state, index) for a compatible checkpoint, None when absent; raise when incompatible."""
        if not self.exists():
            return None
        try:
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
            recorded = index["fingerprint"]
            state_path = self.directory / index["state_file"]
            if recorded != fingerprint:
                differing = sorted(key for key in set(recorded) | set(fingerprint) if recorded.get(key) != fingerprint.get(key))
                raise IncompatibleCheckpointError(
                    f"Checkpoint in {self.directory} was created with different {', '.join(differing)}; "
                    "restore the original inputs and options, or discard the checkpoint to start again."
                )
            payload = state_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != index["state_sha256"]:
                raise IncompatibleCheckpointError(f"Checkpoint state in {self.directory} is corrupt; discard it to start again.")
            return pickle.loads(payload), index
        except IncompatibleCheckpointError:
            raise
        except (OSError, KeyError, TypeError, ValueError, pickle.UnpicklingError, EOFError, AttributeError) as exc:
            raise IncompatibleCheckpointError(
                f"Checkpoint in {self.directory} is unreadable ({type(exc).__name__}); discard it to start again."
            ) from exc

    def save(self, state: Any, fingerprint: dict[str, Any], *, completed_stages: list[str], run_ids: list[str]) -> dict[str, Any]:
        previous = json.loads(self.index_path.read_text(encoding="utf-8")).get("state_file") if self.exists() else None
        state_file = f"state-{len(completed_stages):02d}-{completed_stages[-1]}.pkl"
        # Stream to disk while hashing: a batch state is hundreds of megabytes, and pickling it to
        # an in-memory bytes object first would hold a second copy at the run's memory peak.
        digest, size = _atomic_pickle(self.directory / state_file, state)
        index = {
            "checkpoint_version": CHECKPOINT_VERSION,
            "fingerprint": fingerprint,
            "completed_stages": completed_stages,
            "run_ids": run_ids,
            "state_file": state_file,
            "state_sha256": digest,
            "state_bytes": size,
        }
        atomic_write_json(self.index_path, index)
        if previous and previous != state_file:
            (self.directory / previous).unlink(missing_ok=True)
        return index

    def discard(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)
