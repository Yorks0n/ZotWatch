from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import shutil
from typing import Callable, Protocol
from urllib.parse import quote
from uuid import uuid4
import zipfile

import requests

from .checkpoint import (
    MAX_CHECKPOINT_BYTES,
    CheckpointExpectation,
    ImportedCheckpoint,
    import_checkpoint,
)


CHECKPOINT_ARTIFACT_NAME = "zotwatch-state-checkpoint-v1"
TRUSTED_EVENTS = frozenset({"schedule", "workflow_dispatch"})
MAX_CANDIDATE_RUNS = 15
GITHUB_API_ROOT = "https://api.github.com"


class ArtifactTransportError(RuntimeError):
    """GitHub artifact discovery or download failed its closed contract."""


@dataclass(frozen=True)
class PriorRunContext:
    repository: str
    repository_id: int
    workflow_id: int
    workflow_path: str
    current_run_id: int
    ref: str
    event: str


@dataclass(frozen=True)
class WorkflowRun:
    run_id: int
    repository_id: int
    workflow_id: int
    workflow_path: str
    event: str
    status: str
    head_ref: str


@dataclass(frozen=True)
class ArtifactDescriptor:
    artifact_id: int
    name: str
    expired: bool
    run_id: int
    digest: str | None


@dataclass(frozen=True)
class RestoreOutcome:
    restored: bool
    source_run_id: int | None
    artifact_id: int | None


class ArtifactSource(Protocol):
    def verify_current_run(self, context: PriorRunContext) -> None: ...

    def list_completed_runs(
        self, context: PriorRunContext, limit: int
    ) -> list[WorkflowRun]: ...

    def list_run_artifacts(
        self, context: PriorRunContext, run_id: int
    ) -> list[ArtifactDescriptor]: ...

    def download_artifact(
        self, context: PriorRunContext, artifact: ArtifactDescriptor, destination: Path
    ) -> Path: ...


def restore_prior_checkpoint(
    source: ArtifactSource,
    context: PriorRunContext,
    state_root: Path | str,
    staging_root: Path | str,
    *,
    expectation_factory: Callable[[WorkflowRun], CheckpointExpectation],
    max_candidates: int = MAX_CANDIDATE_RUNS,
) -> RestoreOutcome:
    """Restore the first valid checkpoint in a bounded newest-first run search."""

    if max_candidates < 1 or max_candidates > MAX_CANDIDATE_RUNS:
        raise ArtifactTransportError("Checkpoint search limit is outside the closed bound")
    _validate_context(context)
    try:
        source.verify_current_run(context)
        candidates = source.list_completed_runs(context, max_candidates)
    except Exception as exc:
        raise ArtifactTransportError("Current run or checkpoint discovery could not be verified") from exc
    for run in candidates[:max_candidates]:
        if not _eligible_run(run, context):
            continue
        try:
            matching = [
                artifact
                for artifact in source.list_run_artifacts(context, run.run_id)
                if artifact.name == CHECKPOINT_ARTIFACT_NAME
                and not artifact.expired
                and artifact.run_id == run.run_id
            ]
        except Exception:
            continue
        if len(matching) != 1:
            continue
        artifact = matching[0]
        candidate = Path(staging_root) / f"{artifact.artifact_id}-{uuid4().hex}"
        try:
            downloaded = source.download_artifact(context, artifact, candidate)
            import_checkpoint(downloaded, state_root, expectation_factory(run))
        except Exception:
            shutil.rmtree(candidate, ignore_errors=True)
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        return RestoreOutcome(True, run.run_id, artifact.artifact_id)
    return RestoreOutcome(False, None, None)


def _validate_context(context: PriorRunContext) -> None:
    if context.event not in TRUSTED_EVENTS:
        raise ArtifactTransportError("Checkpoint restore requires a trusted caller event")
    if not context.ref.startswith("refs/heads/"):
        raise ArtifactTransportError("Checkpoint restore requires a branch ref")
    if context.repository_id <= 0 or context.workflow_id <= 0 or context.current_run_id <= 0:
        raise ArtifactTransportError("Checkpoint restore context is incomplete")


def _eligible_run(run: WorkflowRun, context: PriorRunContext) -> bool:
    return (
        run.run_id != context.current_run_id
        and run.repository_id == context.repository_id
        and run.workflow_id == context.workflow_id
        and run.workflow_path == context.workflow_path
        and run.event in TRUSTED_EVENTS
        and run.status == "completed"
        and run.head_ref == context.ref
    )


class GitHubArtifactSource:
    """Minimal GitHub.com REST client for closed prior-checkpoint discovery."""

    def __init__(self, token: str, *, session: requests.Session | None = None):
        if not token:
            raise ArtifactTransportError("GitHub token is required for private artifacts")
        self.session = session or requests.Session()
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def verify_current_run(self, context: PriorRunContext) -> None:
        raw = self._json(
            f"/repos/{_repository_path(context.repository)}/actions/runs/{context.current_run_id}"
        )
        parsed = _parse_run(raw)
        if not _eligible_current_run(parsed, context):
            raise ArtifactTransportError("Current GitHub run metadata does not match context")

    def list_completed_runs(
        self, context: PriorRunContext, limit: int
    ) -> list[WorkflowRun]:
        if limit > MAX_CANDIDATE_RUNS:
            raise ArtifactTransportError("GitHub run query exceeds the checkpoint bound")
        branch = context.ref.removeprefix("refs/heads/")
        raw = self._json(
            f"/repos/{_repository_path(context.repository)}/actions/workflows/"
            f"{context.workflow_id}/runs",
            params={"branch": branch, "status": "completed", "per_page": limit, "page": 1},
        )
        runs = raw.get("workflow_runs") if isinstance(raw, dict) else None
        if not isinstance(runs, list) or len(runs) > limit:
            raise ArtifactTransportError("GitHub workflow-run response is invalid")
        return [_parse_run(item) for item in runs]

    def list_run_artifacts(
        self, context: PriorRunContext, run_id: int
    ) -> list[ArtifactDescriptor]:
        raw = self._json(
            f"/repos/{_repository_path(context.repository)}/actions/runs/{run_id}/artifacts",
            params={"per_page": 100, "page": 1},
        )
        artifacts = raw.get("artifacts") if isinstance(raw, dict) else None
        if not isinstance(artifacts, list):
            raise ArtifactTransportError("GitHub artifact response is invalid")
        if raw.get("total_count") != len(artifacts):
            raise ArtifactTransportError("GitHub artifact response exceeds one bounded page")
        parsed: list[ArtifactDescriptor] = []
        for item in artifacts:
            workflow_run = item.get("workflow_run") or {}
            parsed.append(
                ArtifactDescriptor(
                    artifact_id=_positive_int(item.get("id"), "artifact id"),
                    name=str(item.get("name", "")),
                    expired=item.get("expired") is True,
                    run_id=_positive_int(workflow_run.get("id"), "artifact run id"),
                    digest=item.get("digest") if isinstance(item.get("digest"), str) else None,
                )
            )
        return parsed

    def download_artifact(
        self, context: PriorRunContext, artifact: ArtifactDescriptor, destination: Path
    ) -> Path:
        response = self.session.get(
            f"{GITHUB_API_ROOT}/repos/{_repository_path(context.repository)}/actions/"
            f"artifacts/{artifact.artifact_id}/zip",
            headers=self.headers,
            timeout=30,
        )
        if response.status_code != 200:
            raise ArtifactTransportError("GitHub checkpoint artifact download failed")
        content = response.content
        if len(content) > MAX_CHECKPOINT_BYTES:
            raise ArtifactTransportError("GitHub checkpoint archive is too large")
        if artifact.digest is not None:
            algorithm, separator, expected = artifact.digest.partition(":")
            if algorithm != "sha256" or not separator or len(expected) != 64:
                raise ArtifactTransportError("GitHub artifact digest metadata is invalid")
            if hashlib.sha256(content).hexdigest() != expected:
                raise ArtifactTransportError("GitHub artifact archive digest does not match")
        destination.mkdir(parents=True)
        _safe_extract(content, destination)
        return destination

    def _json(self, path: str, *, params: dict | None = None) -> dict:
        response = self.session.get(
            f"{GITHUB_API_ROOT}{path}", headers=self.headers, params=params, timeout=30
        )
        if response.status_code != 200:
            raise ArtifactTransportError("GitHub Actions metadata request failed")
        try:
            value = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ArtifactTransportError("GitHub Actions metadata is not JSON") from exc
        if not isinstance(value, dict):
            raise ArtifactTransportError("GitHub Actions metadata is invalid")
        return value


def _eligible_current_run(run: WorkflowRun, context: PriorRunContext) -> bool:
    return (
        run.run_id == context.current_run_id
        and run.repository_id == context.repository_id
        and run.workflow_id == context.workflow_id
        and run.workflow_path == context.workflow_path
        and run.event == context.event
        and run.head_ref == context.ref
    )


def _parse_run(raw: object) -> WorkflowRun:
    if not isinstance(raw, dict):
        raise ArtifactTransportError("GitHub workflow-run entry is invalid")
    repository = raw.get("repository") or raw.get("head_repository") or {}
    path = str(raw.get("path", "")).split("@", 1)[0]
    head_branch = raw.get("head_branch")
    return WorkflowRun(
        run_id=_positive_int(raw.get("id"), "run id"),
        repository_id=_positive_int(repository.get("id"), "repository id"),
        workflow_id=_positive_int(raw.get("workflow_id"), "workflow id"),
        workflow_path=path,
        event=str(raw.get("event", "")),
        status=str(raw.get("status", "")),
        head_ref=f"refs/heads/{head_branch}" if isinstance(head_branch, str) else "",
    )


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArtifactTransportError(f"GitHub {label} is invalid")
    return value


def _repository_path(repository: str) -> str:
    parts = repository.split("/")
    if len(parts) != 2 or any(not part for part in parts):
        raise ArtifactTransportError("GitHub repository identity is invalid")
    return "/".join(quote(part, safe="") for part in parts)


def _safe_extract(content: bytes, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ArtifactTransportError("GitHub checkpoint artifact is not a ZIP archive") from exc
    total = 0
    with archive:
        for member in archive.infolist():
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ArtifactTransportError("GitHub artifact contains an unsafe path")
            if member.is_dir():
                continue
            mode = member.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ArtifactTransportError("GitHub artifact contains a symlink")
            total += member.file_size
            if total > MAX_CHECKPOINT_BYTES:
                raise ArtifactTransportError("GitHub checkpoint contents are too large")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


__all__ = [
    "CHECKPOINT_ARTIFACT_NAME", "MAX_CANDIDATE_RUNS", "ArtifactDescriptor",
    "ArtifactTransportError", "GitHubArtifactSource", "PriorRunContext",
    "RestoreOutcome", "WorkflowRun", "restore_prior_checkpoint",
]
