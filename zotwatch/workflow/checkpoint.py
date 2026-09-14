from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.computational_state import CurrentPointer, StateCoordinator, StateManifest, sha256_file


CHECKPOINT_SCHEMA_NAME = "zotwatch-state-checkpoint"
CHECKPOINT_SCHEMA_VERSION = 1
MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024 * 1024


class CheckpointError(RuntimeError):
    """A private computational-state checkpoint failed its closed contract."""


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CheckpointContext(_ClosedModel):
    source_result_status: Literal["succeeded"]
    source_engine_run_id: str = Field(min_length=1, max_length=128)
    engine_repository: str = Field(min_length=3, max_length=256)
    engine_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    workspace_repository: str = Field(min_length=3, max_length=256)
    workspace_repository_id: int = Field(gt=0)
    caller_workflow_id: int = Field(gt=0)
    caller_workflow_path: str = Field(min_length=1, max_length=512)
    caller_event: Literal["schedule", "workflow_dispatch"]
    caller_run_id: int = Field(gt=0)
    caller_run_attempt: int = Field(gt=0)
    caller_ref: str = Field(pattern=r"^refs/heads/[A-Za-z0-9._/-]+$")
    config_fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CheckpointExpectation(_ClosedModel):
    engine_repository: str
    workspace_repository: str
    workspace_repository_id: int
    caller_workflow_id: int
    caller_workflow_path: str
    caller_event: Literal["schedule", "workflow_dispatch"]
    caller_run_id: int
    caller_ref: str
    config_fingerprint_sha256: str


class CheckpointEntry(_ClosedModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=MAX_CHECKPOINT_BYTES)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value in {"", "."}:
            raise ValueError("checkpoint entry path must be safe and relative")
        return value


class StateBinding(_ClosedModel):
    library_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    library_revision: int = Field(ge=0)
    generation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    state_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CheckpointManifest(_ClosedModel):
    schema_name: Literal["zotwatch-state-checkpoint"] = CHECKPOINT_SCHEMA_NAME
    schema_version: Literal[1] = CHECKPOINT_SCHEMA_VERSION
    created_at: str
    source_result_status: Literal["succeeded"]
    source_engine_run_id: str
    engine_repository: str
    engine_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    workspace_repository: str
    workspace_repository_id: int
    caller_workflow_id: int
    caller_workflow_path: str
    caller_event: Literal["schedule", "workflow_dispatch"]
    caller_run_id: int
    caller_run_attempt: int
    caller_ref: str
    config_fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: StateBinding
    entries: tuple[CheckpointEntry, ...]


class ImportedCheckpoint(_ClosedModel):
    library_identity_sha256: str
    library_revision: int
    generation_id: str
    source_run_id: int


def export_checkpoint(
    state_root: Path | str,
    destination: Path | str,
    context: CheckpointContext,
) -> Path:
    """Export only the SQLite mirror and the current immutable E4 generation."""

    root = Path(state_root).resolve()
    target = Path(destination).resolve()
    if target.exists():
        raise CheckpointError("Checkpoint destination already exists")
    staging = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with StateCoordinator(root).acquire():
            pointer = _read_model(CurrentPointer, root / "computational/current.json", "current pointer")
            generation = root / "computational/generations" / pointer.generation_id
            manifest_path = generation / "state-manifest.json"
            manifest = _read_model(StateManifest, manifest_path, "state manifest")
            if sha256_file(manifest_path) != pointer.manifest_sha256:
                raise CheckpointError("Current pointer manifest checksum is invalid")
            if manifest.generation.id != pointer.generation_id:
                raise CheckpointError("Current pointer and state manifest disagree")
            source_database = root / "profile.sqlite"
            identity, revision = _sqlite_binding(source_database)
            if (
                manifest.library.identity_sha256 != identity
                or manifest.library.revision != revision
            ):
                raise CheckpointError("SQLite mirror and current generation disagree")

            staging.mkdir(parents=True)
            _sqlite_backup(source_database, staging / "profile.sqlite")
            pointer_target = staging / "computational/current.json"
            pointer_target.parent.mkdir(parents=True)
            shutil.copyfile(root / "computational/current.json", pointer_target)
            generation_target = staging / "computational/generations" / pointer.generation_id
            generation_target.mkdir(parents=True)
            for filename in (
                "state-manifest.json", "profile.json", "embeddings.npz", "faiss.index"
            ):
                source = generation / filename
                if not source.is_file() or source.is_symlink():
                    raise CheckpointError(f"Current generation artifact is invalid: {filename}")
                shutil.copyfile(source, generation_target / filename)

            entry_paths = _expected_entry_paths(pointer.generation_id)
            entries = tuple(_entry(staging, path) for path in entry_paths)
            checkpoint = CheckpointManifest(
                created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                state=StateBinding(
                    library_identity_sha256=identity,
                    library_revision=revision,
                    generation_id=pointer.generation_id,
                    state_manifest_sha256=pointer.manifest_sha256,
                ),
                entries=entries,
                **context.model_dump(),
            )
            _write_json(staging / "checkpoint.json", checkpoint.model_dump(mode="json"))
            _validate_bundle(staging, checkpoint)
        os.replace(staging, target)
        return target
    except CheckpointError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise CheckpointError("Computational-state checkpoint export failed") from exc


def import_checkpoint(
    bundle_root: Path | str,
    state_root: Path | str,
    expectation: CheckpointExpectation,
) -> ImportedCheckpoint:
    """Validate an untrusted bundle completely before atomically installing it."""

    bundle = Path(bundle_root).resolve()
    target = Path(state_root).resolve()
    if target.exists() and any(target.iterdir()):
        raise CheckpointError("Checkpoint import requires a new empty state directory")
    checkpoint = _read_model(
        CheckpointManifest, bundle / "checkpoint.json", "checkpoint manifest"
    )
    _validate_expectation(checkpoint, expectation)
    _validate_bundle(bundle, checkpoint)
    identity, revision = _sqlite_binding(bundle / "profile.sqlite")
    if identity != checkpoint.state.library_identity_sha256 or revision != checkpoint.state.library_revision:
        raise CheckpointError("Checkpoint SQLite binding does not match its manifest")

    pointer = _read_model(CurrentPointer, bundle / "computational/current.json", "current pointer")
    if (
        pointer.generation_id != checkpoint.state.generation_id
        or pointer.manifest_sha256 != checkpoint.state.state_manifest_sha256
    ):
        raise CheckpointError("Checkpoint current pointer does not match its state binding")
    generation = bundle / "computational/generations" / pointer.generation_id
    state_manifest = _read_model(StateManifest, generation / "state-manifest.json", "state manifest")
    if (
        state_manifest.library.identity_sha256 != identity
        or state_manifest.library.revision != revision
        or state_manifest.generation.id != pointer.generation_id
    ):
        raise CheckpointError("Checkpoint state generation does not match SQLite")

    installation = target.with_name(f".{target.name}.{uuid4().hex}.restore")
    try:
        installation.mkdir(parents=True)
        shutil.copyfile(bundle / "profile.sqlite", installation / "profile.sqlite")
        shutil.copytree(bundle / "computational", installation / "computational")
        _sqlite_binding(installation / "profile.sqlite")
        if target.exists():
            target.rmdir()
        os.replace(installation, target)
    except Exception as exc:
        shutil.rmtree(installation, ignore_errors=True)
        raise CheckpointError("Validated checkpoint could not be installed") from exc
    return ImportedCheckpoint(
        library_identity_sha256=identity,
        library_revision=revision,
        generation_id=pointer.generation_id,
        source_run_id=checkpoint.caller_run_id,
    )


def _validate_expectation(
    checkpoint: CheckpointManifest, expectation: CheckpointExpectation
) -> None:
    actual = {
        field: getattr(checkpoint, field)
        for field in expectation.model_fields
    }
    if actual != expectation.model_dump():
        raise CheckpointError("Checkpoint namespace does not match this workflow run")


def _validate_bundle(root: Path, checkpoint: CheckpointManifest) -> None:
    expected = _expected_entry_paths(checkpoint.state.generation_id)
    actual = tuple(entry.path for entry in checkpoint.entries)
    if actual != expected or len(set(actual)) != len(actual):
        raise CheckpointError("Checkpoint entry allowlist is incomplete or reordered")
    total = 0
    for entry in checkpoint.entries:
        path = root.joinpath(*PurePosixPath(entry.path).parts)
        if path.is_symlink() or not path.is_file():
            raise CheckpointError("Checkpoint entry is missing or is a symlink")
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise CheckpointError("Checkpoint entry escapes its bundle") from exc
        stat = path.stat()
        total += stat.st_size
        if stat.st_size != entry.size_bytes or sha256_file(path) != entry.sha256:
            raise CheckpointError("Checkpoint entry size or checksum does not match")
    if total > MAX_CHECKPOINT_BYTES:
        raise CheckpointError("Checkpoint exceeds the maximum supported size")
    files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if files != {"checkpoint.json", *expected}:
        raise CheckpointError("Checkpoint contains files outside the closed allowlist")


def _expected_entry_paths(generation_id: str) -> tuple[str, ...]:
    prefix = f"computational/generations/{generation_id}"
    return (
        "profile.sqlite",
        "computational/current.json",
        f"{prefix}/state-manifest.json",
        f"{prefix}/profile.json",
        f"{prefix}/embeddings.npz",
        f"{prefix}/faiss.index",
    )


def _entry(root: Path, relative: str) -> CheckpointEntry:
    path = root.joinpath(*PurePosixPath(relative).parts)
    return CheckpointEntry(
        path=relative, sha256=sha256_file(path), size_bytes=path.stat().st_size
    )


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    if source_path.is_symlink() or not source_path.is_file():
        raise CheckpointError("Profile SQLite mirror is missing")
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise CheckpointError("SQLite backup failed integrity validation")
    finally:
        destination.close()
        source.close()


def _sqlite_binding(path: Path) -> tuple[str, int]:
    if path.is_symlink() or not path.is_file():
        raise CheckpointError("Profile SQLite mirror is missing")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise CheckpointError("Profile SQLite mirror is corrupt")
        rows = dict(
            connection.execute(
                "SELECT key, value FROM metadata WHERE key IN (?, ?)",
                ("library_identity_sha256", "last_modified_version"),
            ).fetchall()
        )
    except sqlite3.Error as exc:
        raise CheckpointError("Profile SQLite metadata cannot be read") from exc
    finally:
        connection.close()
    identity = rows.get("library_identity_sha256")
    revision_text = rows.get("last_modified_version")
    if not isinstance(identity, str) or len(identity) != 64:
        raise CheckpointError("Profile SQLite library identity is missing")
    try:
        revision = int(revision_text)
    except (TypeError, ValueError) as exc:
        raise CheckpointError("Profile SQLite library revision is missing") from exc
    return identity, revision


def _read_model(model, path: Path, label: str):
    if path.is_symlink() or not path.is_file():
        raise CheckpointError(f"{label} is missing")
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise CheckpointError(f"{label} is invalid") from exc


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "CHECKPOINT_SCHEMA_NAME", "CHECKPOINT_SCHEMA_VERSION", "CheckpointContext",
    "CheckpointEntry", "CheckpointError", "CheckpointExpectation",
    "CheckpointManifest", "ImportedCheckpoint", "export_checkpoint",
    "import_checkpoint",
]
