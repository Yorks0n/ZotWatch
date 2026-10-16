import json

from zotwatch.results.recorder import RunRecorder


def test_private_manifest_finalizes_stages_and_returns_relative_reference(tmp_path):
    recorder = RunRecorder(
        tmp_path, "watch", config_schema_version=2,
        config_fingerprint_sha256="a" * 64, run_id="run-1",
    )
    recorder.start("config_validation")
    recorder.finish("config_validation")
    recorder.start("candidate_fetch")
    recorder.degrade("candidate_fetch", "CANDIDATE_PARTIAL", count=2)
    result = recorder.finalize(status="degraded", exit_code=0)
    assert result.manifest_path == "runs/run-1.json"
    manifest = json.loads((tmp_path / result.manifest_path).read_text())
    assert manifest["status"] == "degraded"
    assert all(stage["status"] not in {"pending", "running"} for stage in manifest["stages"])
    text = json.dumps(manifest)
    assert str(tmp_path) not in text
    assert "CANDIDATE_PARTIAL" in text


def test_error_catalog_never_serializes_raw_exception(tmp_path):
    recorder = RunRecorder(
        tmp_path, "profile", config_schema_version=None,
        config_fingerprint_sha256=None, run_id="run-2",
    )
    recorder.fail("zotero_sync", "unknown user controlled secret")
    result = recorder.finalize(status="failed", exit_code=4, error_code="unknown user controlled secret")
    payload = (tmp_path / result.manifest_path).read_text()
    assert "unknown user controlled secret" not in payload
    assert payload.count("INTERNAL_ERROR") >= 1
