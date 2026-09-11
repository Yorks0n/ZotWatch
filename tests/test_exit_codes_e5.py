import json
from types import SimpleNamespace

from src import cli as engine_cli
from src import fetch_new
from src.storage import ProfileStorage
from zotwatch import cli
from zotwatch.results.models import ArtifactReference, RunError, RunResult
from zotwatch.results.recorder import RunRecorder

from .test_runtime_cli_e5a import credentials, write_config


def test_machine_result_is_single_closed_json_object(workspace, monkeypatch, capsys):
    write_config(workspace, formats=("json",))
    credentials(monkeypatch)
    artifact = ArtifactReference(
        path=".zotwatch-output/generations/run-1/recommendations.json",
        sha256="a" * 64,
        size_bytes=42,
        media_type="application/json",
        publishable=True,
    )
    monkeypatch.setattr(
        engine_cli,
        "run_watch_recorded",
        lambda *args, **kwargs: RunResult(
            run_id="run-1", status="succeeded", exit_code=0,
            manifest_path="runs/run-1.json", output_generation_id="run-1",
            artifacts=[artifact],
        ),
    )
    assert cli.main([
        "watch", "--workspace", str(workspace), "--machine-result"
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_name"] == "zotwatch-run-result"
    assert payload["artifacts"][0]["path"].startswith(".zotwatch-output/generations/")
    assert str(workspace) not in json.dumps(payload)


def test_machine_failure_preserves_symbolic_code_and_numeric_category(workspace, monkeypatch, capsys):
    write_config(workspace, formats=("rss",))
    credentials(monkeypatch)
    monkeypatch.setattr(
        engine_cli,
        "run_watch_recorded",
        lambda *args, **kwargs: RunResult(
            run_id="run-fail", status="failed", exit_code=4,
            error=RunError(code="CANDIDATE_UNAVAILABLE", message="Candidate acquisition was unavailable.", diagnostic_id="d1"),
        ),
    )
    assert cli.main([
        "watch", "--workspace", str(workspace), "--machine-result"
    ]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CANDIDATE_UNAVAILABLE"


def test_machine_preflight_failure_is_recorded_without_secret_slot_names(workspace, monkeypatch, capsys):
    write_config(workspace, formats=("json",))
    monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
    monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
    assert cli.main([
        "watch", "--workspace", str(workspace), "--machine-result"
    ]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "CREDENTIAL_MISSING"
    serialized = json.dumps(payload)
    assert "ZOTERO_USER_ID" not in serialized
    assert "ZOTERO_API_KEY" not in serialized
    assert (workspace / "data" / payload["manifest_path"]).is_file()


def test_strict_degradation_does_not_publish_outputs(workspace, settings, monkeypatch):
    state = workspace / "state"
    reports = workspace / "reports"
    storage = ProfileStorage(state / "profile.sqlite")
    storage.initialize()
    handle = SimpleNamespace(generation_id="state-1", profile={})
    monkeypatch.setattr(engine_cli.ZoteroIngestor, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine_cli, "_ensure_computational_state", lambda *args, **kwargs: (handle, None))
    monkeypatch.setattr(
        fetch_new.CandidateFetcher,
        "fetch_with_outcome",
        lambda self: fetch_new.CandidateFetchOutcome([], "degraded", True, False, 1),
    )
    monkeypatch.setattr(engine_cli.DedupeEngine, "filter", lambda self, values: values)
    monkeypatch.setattr(
        engine_cli, "WorkRanker",
        lambda *args, **kwargs: SimpleNamespace(rank=lambda values: []),
    )
    recorder = RunRecorder(
        state, "watch", config_schema_version=2,
        config_fingerprint_sha256="a" * 64, run_id="strict-run",
    )
    result = engine_cli.run_watch_recorded(
        workspace, settings, storage, state_dir=state, reports_dir=reports,
        output_formats=("json",), top=20, max_preprint_ratio=0.3,
        journal_metrics="bundled", strict=True, recorder=recorder,
    )
    storage.close()
    assert result.status == "degraded"
    assert result.exit_code == 5
    assert not (reports / ".zotwatch-output/latest-success.json").exists()


def test_machine_config_failure_has_private_manifest(workspace, capsys):
    (workspace / "config").rename(workspace / "legacy-config")
    (workspace / "zotwatch.yaml").write_text("schema_version: 2\nunknown: true\n")
    assert cli.main([
        "watch", "--workspace", str(workspace), "--machine-result"
    ]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "CONFIG_INVALID"
    assert (workspace / "data" / payload["manifest_path"]).is_file()
