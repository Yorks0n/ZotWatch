from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import shutil
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError
from pydantic import BaseModel, ConfigDict, Field

from zotwatch.results.models import ArtifactReference, RunManifest, RunResult


PUBLISHABLE_MEDIA = {
    "recommendations.json": "application/json",
    "feed.xml": "application/rss+xml",
    "report.html": "text/html; charset=utf-8",
}


class WorkflowResultError(RuntimeError):
    """Machine result and its immutable artifacts do not form a valid E5 result."""


class WorkflowEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_name: Literal["zotwatch-workflow-envelope"] = "zotwatch-workflow-envelope"
    schema_version: Literal[1] = 1
    engine_repository: str
    engine_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    workspace_repository: str
    workspace_repository_id: int = Field(gt=0)
    caller_run_id: int = Field(gt=0)
    run_id: str
    result_status: Literal["succeeded", "degraded", "failed"]
    publish_requested: bool


@dataclass(frozen=True)
class ValidatedWorkflowResult:
    result: RunResult
    manifest: RunManifest
    publishable_directory: Path
    private_directory: Path | None


def seal_private_result(
    private_root: Path | str,
    *,
    engine_repository: str,
    engine_sha: str,
    workspace_repository: str,
    workspace_repository_id: int,
    caller_run_id: int,
    run_id: str,
    result_status: str,
    publish_requested: bool,
) -> WorkflowEnvelope:
    root = Path(private_root)
    if not root.is_dir():
        raise WorkflowResultError("Private result staging is missing")
    envelope = WorkflowEnvelope(
        engine_repository=engine_repository,
        engine_sha=engine_sha,
        workspace_repository=workspace_repository,
        workspace_repository_id=workspace_repository_id,
        caller_run_id=caller_run_id,
        run_id=run_id,
        result_status=result_status,
        publish_requested=publish_requested,
    )
    target = root / "workflow-envelope.json"
    if target.exists():
        raise WorkflowResultError("Private result is already sealed")
    target.write_text(envelope.model_dump_json() + "\n", encoding="utf-8")
    return envelope


def materialize_pages_payload(
    private_root: Path | str,
    report_root: Path | str,
    destination: Path | str,
    *,
    expected_engine_repository: str,
    expected_engine_sha: str,
    expected_workspace_repository: str,
    expected_workspace_repository_id: int,
    expected_caller_run_id: int,
    expected_run_id: str,
) -> Path:
    """Revalidate two exact current-run artifacts and build a Pages allowlist."""

    private = Path(private_root).resolve()
    reports = Path(report_root).resolve()
    try:
        envelope = WorkflowEnvelope.model_validate_json(
            (private / "workflow-envelope.json").read_text(encoding="utf-8")
        )
        result = RunResult.model_validate_json(
            (private / "final/machine-result.json").read_text(encoding="utf-8")
        )
        manifest = RunManifest.model_validate_json(
            (private / "final/run-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise WorkflowResultError("Private Pages input contracts are invalid") from exc
    expected = (
        expected_engine_repository,
        expected_engine_sha,
        expected_workspace_repository,
        expected_workspace_repository_id,
        expected_caller_run_id,
        expected_run_id,
    )
    actual = (
        envelope.engine_repository,
        envelope.engine_sha,
        envelope.workspace_repository,
        envelope.workspace_repository_id,
        envelope.caller_run_id,
        envelope.run_id,
    )
    if actual != expected or envelope.result_status != "succeeded" or not envelope.publish_requested:
        raise WorkflowResultError("Pages deployment identity or opt-in does not match")
    if (
        result.run_id != expected_run_id
        or result.status != "succeeded"
        or result != RunResult(
            run_id=manifest.run_id,
            status=manifest.status,
            exit_code=manifest.exit_code,
            error=manifest.error,
            manifest_path="runs/" + manifest.run_id + ".json",
            state_generation_id=manifest.state_generation_id,
            output_generation_id=manifest.output_generation_id,
            artifacts=manifest.artifacts,
        )
    ):
        raise WorkflowResultError("Pages RunResult and private manifest disagree")
    copies: dict[str, bytes] = {}
    for artifact in result.artifacts:
        name = PurePosixPath(artifact.path).name
        if not artifact.publishable or PUBLISHABLE_MEDIA.get(name) != artifact.media_type:
            raise WorkflowResultError("Pages input is outside the publishable allowlist")
        source = reports / name
        if source.is_symlink() or not source.is_file():
            raise WorkflowResultError("Pages artifact is missing")
        content = source.read_bytes()
        if len(content) != artifact.size_bytes or hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise WorkflowResultError("Pages artifact hash does not match RunResult")
        copies[name] = content
    if not copies:
        raise WorkflowResultError("Pages deployment has no publishable artifacts")
    output = Path(destination).resolve()
    _replace_directory(output, copies)
    return output


def validate_and_materialize_result(
    machine_result_path: Path | str,
    *,
    process_exit_code: int,
    state_root: Path | str,
    reports_root: Path | str,
    publishable_destination: Path | str,
    private_destination: Path | str | None = None,
) -> ValidatedWorkflowResult:
    """Validate E5 authority and copy only its declared immutable allowlist."""

    machine_path = Path(machine_result_path)
    state = Path(state_root).resolve()
    reports = Path(reports_root).resolve()
    try:
        result = RunResult.model_validate_json(machine_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise WorkflowResultError("Engine invocation did not finalize a valid RunResult") from exc
    _validate_exit_semantics(result, process_exit_code)
    if result.manifest_path is None:
        raise WorkflowResultError("RunResult does not reference a private run manifest")
    manifest_path = _safe_join(state, result.manifest_path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise WorkflowResultError("RunResult private manifest is missing")
    try:
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise WorkflowResultError("RunResult private manifest is invalid") from exc
    if (
        manifest.run_id != result.run_id
        or manifest.status != result.status
        or manifest.exit_code != result.exit_code
        or manifest.state_generation_id != result.state_generation_id
        or manifest.output_generation_id != result.output_generation_id
        or manifest.artifacts != result.artifacts
    ):
        raise WorkflowResultError("RunResult and private manifest disagree")

    publishable = Path(publishable_destination).resolve()
    private = Path(private_destination).resolve() if private_destination is not None else None
    _materialize_publishable(result, manifest.command, reports, publishable)
    if private is not None:
        _materialize_private(result, manifest, private)
    return ValidatedWorkflowResult(result, manifest, publishable, private)


def _validate_exit_semantics(result: RunResult, process_exit_code: int) -> None:
    if result.exit_code != process_exit_code:
        raise WorkflowResultError("Process exit code and RunResult disagree")
    expected = {"succeeded": 0, "degraded": 5}
    if result.status in expected and result.exit_code != expected[result.status]:
        raise WorkflowResultError("RunResult status and exit code disagree")
    if result.status == "failed" and result.exit_code == 0:
        raise WorkflowResultError("Failed RunResult cannot use a successful exit code")
    if result.status == "succeeded" and result.error is not None:
        raise WorkflowResultError("Successful RunResult cannot contain a terminal error")


def _materialize_publishable(
    result: RunResult, command: str, reports: Path, destination: Path
) -> None:
    if result.status == "failed":
        if result.artifacts:
            raise WorkflowResultError("Failed RunResult cannot publish recommendation artifacts")
        _replace_directory(destination, {})
        return
    if command == "profile":
        if result.output_generation_id is not None or result.artifacts:
            raise WorkflowResultError("Profile RunResult cannot declare recommendation outputs")
        _replace_directory(destination, {})
        return
    if result.status == "degraded" and not result.artifacts:
        if result.output_generation_id is not None:
            raise WorkflowResultError("Degraded result has an output generation without artifacts")
        _replace_directory(destination, {})
        return
    if not result.output_generation_id or not result.artifacts:
        raise WorkflowResultError("Successful or degraded result lacks immutable output artifacts")
    expected_prefix = PurePosixPath(
        ".zotwatch-output", "generations", result.output_generation_id
    )
    copies: dict[str, bytes] = {}
    for artifact in result.artifacts:
        if not artifact.publishable:
            raise WorkflowResultError("RunResult contains a non-publishable output reference")
        relative = PurePosixPath(artifact.path)
        if relative.parent != expected_prefix:
            raise WorkflowResultError("RunResult artifact is outside its immutable generation")
        name = relative.name
        if name not in PUBLISHABLE_MEDIA or artifact.media_type != PUBLISHABLE_MEDIA[name]:
            raise WorkflowResultError("RunResult artifact is outside the publishable allowlist")
        if name in copies:
            raise WorkflowResultError("RunResult contains a duplicate output artifact")
        source = _safe_join(reports, artifact.path)
        if source.is_symlink() or not source.is_file():
            raise WorkflowResultError("Declared immutable output artifact is missing")
        content = source.read_bytes()
        if len(content) != artifact.size_bytes or hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise WorkflowResultError("Declared immutable output artifact failed verification")
        copies[name] = content
    _replace_directory(destination, copies)


def _materialize_private(result: RunResult, manifest: RunManifest, destination: Path) -> None:
    files = {
        "machine-result.json": (
            json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode(),
        "run-manifest.json": (
            json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode(),
    }
    _replace_directory(destination, files)


def _replace_directory(destination: Path, files: dict[str, bytes]) -> None:
    if destination.exists():
        raise WorkflowResultError("Workflow materialization destination already exists")
    staging = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        staging.mkdir(parents=True)
        for name, content in files.items():
            target = staging / name
            with target.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(staging, destination)
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, WorkflowResultError):
            raise
        raise WorkflowResultError("Workflow result materialization failed") from exc


def _safe_join(root: Path, relative: str) -> Path:
    path = root.joinpath(*PurePosixPath(relative).parts).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WorkflowResultError("Result path escapes its declared root") from exc
    return path


__all__ = [
    "PUBLISHABLE_MEDIA", "ValidatedWorkflowResult", "WorkflowEnvelope",
    "WorkflowResultError", "materialize_pages_payload", "seal_private_result",
    "validate_and_materialize_result",
]
