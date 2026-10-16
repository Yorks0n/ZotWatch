"""Versioned workflow transport and P5C2 deployment gates, no live credentials."""
import io
import json
from pathlib import Path
import tarfile

import pytest

from zotwatch.interests import runner, staging_runtime
from zotwatch.interests.contract import InterestError
from zotwatch.interests.recall_integration import LatentRecallUnavailable
from zotwatch.interests.results import parse_result
from zotwatch.interests.workflow_results import materialize, parse_workflow_result
from zotwatch.workflow.cli import main
from .test_latent_recall_integration_p5b5 import (
    pipeline, integrated_args, works, install_collection, install_encoding, ranking,
)


def integrated_result(pipeline, monkeypatch):
    paths, effective, model, _ = pipeline
    install_collection(monkeypatch, works())
    install_encoding(monkeypatch, model, [[1., 0.], [0., 1.]])
    result = runner.run(integrated_args(), paths, effective, ranker=ranking)
    assert result.status == "succeeded"
    return paths, result


def test_runner_materialization_sealing_and_suggestions(pipeline, monkeypatch, tmp_path):
    paths, result = integrated_result(pipeline, monkeypatch)
    machine = paths.state / "runs" / f"topic-{result.run_id}.json"
    private = tmp_path / "private"
    summary = tmp_path / "summary.json"
    assert main(["result", "--machine-result", str(machine), "--process-exit-code", "0",
        "--state", str(paths.state), "--reports", str(paths.reports), "--publishable", str(tmp_path / "public"),
        "--private", str(private / "final"), "--summary-output", str(summary)]) == 0
    assert (private / "final/topic-result-v3.json").read_bytes() == machine.read_bytes()
    assert not (tmp_path / "public").exists()
    assert main(["seal-result", "--private", str(private), "--engine-sha", "e" * 40,
        "--repository", "owner/private", "--repository-id", "123", "--caller-run-id", "456",
        "--run-id", result.run_id, "--status", "succeeded", "--publish-requested", "false"]) == 0
    envelope = json.loads((private / "topic-workflow-envelope-v3.json").read_bytes())
    assert envelope["schema_name"] == "zotwatch-latent-topic-workflow-envelope"
    assert envelope["schema_version"] == 3
    assert envelope["evidence"]["latent_recall"] == result.evidence.latent_recall.model_dump()
    assert "recall_decisions" not in envelope["evidence"]
    assert main(["export-suggestions", "--state", str(paths.state), "--machine-result", str(machine),
        "--destination", str(tmp_path / "suggestions"), "--engine-sha", "e" * 40,
        "--repository-id", "123", "--caller-run-id", "456", "--caller-run-attempt", "1"]) == 0


def test_explicit_versions_persistence_and_exit_validation(pipeline, monkeypatch, tmp_path):
    paths, result = integrated_result(pipeline, monkeypatch)
    content = result.model_dump_json().encode()
    assert parse_workflow_result(content) == result
    with pytest.raises(InterestError):
        parse_result(content)  # accepted v2 parser still rejects v3
    for name, version in [("zotwatch-topic-run-result", 3), ("zotwatch-latent-topic-run-result", 2), ("other", 3)]:
        row = result.model_dump()
        row.update(schema_name=name, schema_version=version)
        with pytest.raises(InterestError):
            parse_workflow_result(json.dumps(row).encode())
    machine = paths.state / "runs" / f"topic-{result.run_id}.json"
    with pytest.raises(InterestError):
        materialize(machine, 5, paths.state, tmp_path / "wrong-exit")
    machine.write_text(result.model_dump_json() + " \n")
    copied = tmp_path / "copied.json"
    copied.write_bytes(content)
    with pytest.raises(InterestError):
        materialize(copied, 0, paths.state, tmp_path / "wrong-bytes")


def test_staging_rejects_another_valid_model_before_collection(pipeline, monkeypatch):
    paths, effective, _, _ = pipeline
    install_collection(monkeypatch, [])
    monkeypatch.setattr(staging_runtime, "file_hash", lambda _: staging_runtime.MODEL_SHA256)
    result = runner.run(integrated_args(latent_staging=True), paths, effective,
                        ranker=lambda *a: pytest.fail("cannot rank another frozen revision"))
    assert result.status == "not_ready" and result.reason == "LATENT_RECALL_ARTIFACT_UNAVAILABLE"
    assert not (paths.state / "profile.sqlite").exists()


def test_staging_cache_gate_preserves_model_revision_and_stops_before_sync(pipeline, monkeypatch):
    paths, effective, model, _ = pipeline
    monkeypatch.setattr(staging_runtime, "validate_model", lambda _: model)
    result = runner.run(integrated_args(latent_staging=True, latent_encoder_cache=paths.state / "missing-cache"), paths, effective,
                        ranker=lambda *a: pytest.fail("cannot rank with missing cache"))
    assert result.status == "not_ready" and result.reason == "LATENT_RECALL_ENCODER_UNAVAILABLE"
    assert result.evidence.latent_recall.interest_model_revision == model.interest_model_revision
    assert not (paths.state / "profile.sqlite").exists()


@pytest.mark.parametrize("name,type", [("../model.json", tarfile.REGTYPE), ("model.json", tarfile.SYMTYPE)])
def test_private_bundle_rejects_paths_and_links(tmp_path, name, type):
    bundle = tmp_path / "runtime.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.type = type
        info.size = 2 if type == tarfile.REGTYPE else 0
        archive.addfile(info, io.BytesIO(b"{}") if info.size else None)
    with pytest.raises(ValueError):
        staging_runtime.unpack(bundle, tmp_path / "runtime")
    assert not (tmp_path / "runtime").exists()


def test_default_workflow_policy_and_staging_opt_in_only():
    import yaml
    doc = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/run.yml").read_text())
    assert doc[True]["workflow_call"]["inputs"]["candidate-policy"]["default"] == "confirmed-topic-candidates-v1"
    steps = doc["jobs"]["compute"]["steps"]
    fetch = next(s for s in steps if s["name"] == "Fetch private frozen staging runtime")
    assert "center-recall-v1" in fetch["if"] and "topic-v1" in fetch["if"]
    invoke = next(s for s in steps if s.get("id") == "invoke")
    assert "--latent-staging" in invoke["run"] and "HF_HUB_OFFLINE=1" in invoke["run"]
    upload = next(s for s in steps if s.get("id") == "upload-private")
    assert "zotwatch-private-latent-topic-run-v3" in upload["with"]["name"]
