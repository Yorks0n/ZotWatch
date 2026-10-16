from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class WorkflowIdentityError(RuntimeError):
    """Reusable-workflow identity is missing or does not match the engine checkout."""


@dataclass(frozen=True)
class VerifiedWorkflowIdentity:
    workflow_repository: str
    workflow_sha: str
    engine_sha: str


def verify_workflow_identity(
    *,
    expected_repository: str,
    workflow_repository: str,
    workflow_sha: str,
    engine_path: Path | str,
) -> VerifiedWorkflowIdentity:
    """Bind the checked-out engine to GitHub's exact reusable-workflow identity."""

    if workflow_repository.casefold() != expected_repository.casefold():
        raise WorkflowIdentityError("Reusable workflow repository is not trusted")
    normalized_sha = workflow_sha.lower()
    if not FULL_SHA.fullmatch(normalized_sha):
        raise WorkflowIdentityError("Reusable workflow identity must contain a full commit SHA")
    engine = Path(engine_path).resolve()
    try:
        completed = subprocess.run(
            ["git", "-C", str(engine), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkflowIdentityError("Engine checkout identity could not be verified") from exc
    engine_sha = completed.stdout.strip().lower()
    if engine_sha != normalized_sha:
        raise WorkflowIdentityError("Engine checkout does not match reusable workflow SHA")
    return VerifiedWorkflowIdentity(
        workflow_repository=expected_repository,
        workflow_sha=normalized_sha,
        engine_sha=engine_sha,
    )


__all__ = [
    "FULL_SHA", "VerifiedWorkflowIdentity", "WorkflowIdentityError",
    "verify_workflow_identity",
]
