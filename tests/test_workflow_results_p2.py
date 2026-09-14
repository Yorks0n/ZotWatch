import json

import pytest

from zotwatch.results.models import ArtifactReference, RunManifest, RunResult, StageRecord
from zotwatch.workflow.results import (
    WorkflowResultError,
    materialize_pages_payload,
    seal_private_result,
    validate_and_materialize_result,
)


def _contracts(state, reports, *, status="succeeded", exit_code=0):
    generation = reports / ".zotwatch-output/generations/run-1"
    generation.mkdir(parents=True)
    payload = b'{"schema_name":"zotwatch-recommendations","schema_version":1}\n'
    output = generation / "recommendations.json"
    output.write_bytes(payload)
    import hashlib
    artifact = ArtifactReference(
        path=".zotwatch-output/generations/run-1/recommendations.json",
        sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload),
        media_type="application/json", publishable=True,
    )
    manifest = RunManifest(
        run_id="run-1", command="watch", status=status, exit_code=exit_code,
        generated_at="2026-01-01T00:00:00Z", config_schema_version=2,
        config_fingerprint_sha256="a" * 64, output_generation_id="run-1",
        stages=[StageRecord(stage="config_validation", status="succeeded")],
        artifacts=[artifact],
    )
    manifest_path = state / "runs/run-1.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(manifest.model_dump_json())
    result = RunResult(
        run_id="run-1", status=status, exit_code=exit_code,
        manifest_path="runs/run-1.json", output_generation_id="run-1",
        artifacts=[artifact],
    )
    machine = state.parent / "machine-result.json"
    machine.write_text(result.model_dump_json())
    return machine


def test_materializer_uses_only_declared_immutable_artifacts(tmp_path):
    state = tmp_path / "state"
    reports = tmp_path / "reports"
    machine = _contracts(state, reports)
    (reports / "secret.txt").write_text("must not escape")
    destination = tmp_path / "publishable"

    validated = validate_and_materialize_result(
        machine, process_exit_code=0, state_root=state, reports_root=reports,
        publishable_destination=destination,
    )

    assert validated.result.status == "succeeded"
    assert sorted(p.name for p in destination.iterdir()) == ["recommendations.json"]
    assert not (destination / "secret.txt").exists()


def test_materializer_rejects_exit_or_hash_mismatch(tmp_path):
    state = tmp_path / "state"
    reports = tmp_path / "reports"
    machine = _contracts(state, reports)
    with pytest.raises(WorkflowResultError):
        validate_and_materialize_result(
            machine, process_exit_code=2, state_root=state, reports_root=reports,
            publishable_destination=tmp_path / "out",
        )
    (reports / ".zotwatch-output/generations/run-1/recommendations.json").write_text("tampered")
    with pytest.raises(WorkflowResultError):
        validate_and_materialize_result(
            machine, process_exit_code=0, state_root=state, reports_root=reports,
            publishable_destination=tmp_path / "out2",
        )


def test_pages_revalidates_sealed_current_run_allowlist(tmp_path):
    state = tmp_path / "state"
    reports = tmp_path / "reports"
    machine = _contracts(state, reports)
    private = tmp_path / "private"
    public = tmp_path / "public"
    validate_and_materialize_result(
        machine, process_exit_code=0, state_root=state, reports_root=reports,
        publishable_destination=public, private_destination=private / "final",
    )
    seal_private_result(
        private,
        engine_repository="Yorks0n/ZotWatch", engine_sha="a" * 40,
        workspace_repository="example/private-zotwatch", workspace_repository_id=1234,
        caller_run_id=77, run_id="run-1", result_status="succeeded",
        publish_requested=True,
    )

    pages = materialize_pages_payload(
        private, public, tmp_path / "pages",
        expected_engine_repository="Yorks0n/ZotWatch", expected_engine_sha="a" * 40,
        expected_workspace_repository="example/private-zotwatch",
        expected_workspace_repository_id=1234, expected_caller_run_id=77,
        expected_run_id="run-1",
    )
    assert [path.name for path in pages.iterdir()] == ["recommendations.json"]


def test_pages_requires_explicit_publish_opt_in(tmp_path):
    state = tmp_path / "state"
    reports = tmp_path / "reports"
    machine = _contracts(state, reports)
    private = tmp_path / "private"
    public = tmp_path / "public"
    validate_and_materialize_result(
        machine, process_exit_code=0, state_root=state, reports_root=reports,
        publishable_destination=public, private_destination=private / "final",
    )
    seal_private_result(
        private,
        engine_repository="Yorks0n/ZotWatch", engine_sha="a" * 40,
        workspace_repository="example/private-zotwatch", workspace_repository_id=1234,
        caller_run_id=77, run_id="run-1", result_status="succeeded",
        publish_requested=False,
    )
    with pytest.raises(WorkflowResultError):
        materialize_pages_payload(
            private, public, tmp_path / "pages",
            expected_engine_repository="Yorks0n/ZotWatch", expected_engine_sha="a" * 40,
            expected_workspace_repository="example/private-zotwatch",
            expected_workspace_repository_id=1234, expected_caller_run_id=77,
            expected_run_id="run-1",
        )
