"""Explicitly versioned private workflow results; v2 keeps its accepted meaning."""
import json
from pathlib import Path

from .contract import InterestError
from .results import parse_result
from .integration_results import IntegrationRunResult


def parse_workflow_result(content: bytes):
    try:
        if len(content) > 2097152:
            raise ValueError()
        identity = json.loads(content)
        version = (identity.get("schema_name"), identity.get("schema_version"))
        if version == ("zotwatch-topic-run-result", 2):
            return parse_result(content)
        if version == ("zotwatch-latent-topic-run-result", 3):
            return IntegrationRunResult.model_validate_json(content)
        raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise InterestError("INTEREST_RESULT_INVALID") from None


def materialize(machine: Path, process_exit: int, state: Path, destination: Path):
    try:
        content = machine.read_bytes()
        value = parse_workflow_result(content)
        recorded = state / "runs" / f"topic-{value.run_id}.json"
        if machine.is_symlink() or recorded.is_symlink() or recorded.read_bytes() != content or value.exit_code != process_exit:
            raise ValueError()
        sidecar = state / "runs" / f"latent-deployment-{value.run_id}.json"
        sidecar_content = None
        if sidecar.exists():
            from .lifecycle import RunDeploymentEvidence
            if sidecar.is_symlink() or sidecar.stat().st_size > 16384:
                raise ValueError()
            sidecar_content = sidecar.read_bytes()
            RunDeploymentEvidence.model_validate_json(sidecar_content).validate_result(value)
        destination.mkdir(parents=True, exist_ok=False)
        (destination / f"topic-result-v{value.schema_version}.json").write_bytes(content)
        if sidecar_content is not None:
            (destination / "latent-deployment-evidence-v1.json").write_bytes(sidecar_content)
        return value
    except (OSError, ValueError):
        raise InterestError("INTEREST_RESULT_INVALID") from None


def envelope_evidence(value):
    if value.evidence is None:
        return None
    evidence = value.evidence.model_dump()
    # Candidate decisions remain in the private result, never the small receipt envelope.
    if value.schema_version == 3:
        evidence.pop("recall_decisions")
    return evidence
