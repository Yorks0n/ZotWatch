from copy import deepcopy
import shutil
from types import SimpleNamespace

import yaml

from src import build_profile, cli as legacy_cli, fetch_new, ingest_zotero_api, score_rank
from src.computational_state import pseudonymous_library_identity
from src.models import ZoteroItem
from src.storage import ProfileStorage
from zotwatch import cli
from zotwatch.results.models import RunResult

from .helpers import FIXTURES, FixedVectors, read_json
from .test_config_v2 import minimal_config


def write_config(workspace, formats=("rss", "html"), *, ai=None):
    if (workspace / "config").exists():
        shutil.rmtree(workspace / "config")
    data = deepcopy(minimal_config())
    data["outputs"]["formats"] = list(formats)
    if ai is not None:
        data["ai"] = ai
    (workspace / "zotwatch.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def credentials(monkeypatch):
    monkeypatch.setenv("ZOTERO_USER_ID", "12345")
    monkeypatch.setenv("ZOTERO_API_KEY", "synthetic-zotero-key")


def test_v2_watch_dispatches_existing_pipeline_with_config_values(
    workspace, monkeypatch
):
    write_config(workspace)
    credentials(monkeypatch)
    calls = []
    monkeypatch.setattr(
        legacy_cli,
        "run_watch_recorded",
        lambda base, settings, storage, **kwargs: (
            calls.append((base, settings, kwargs))
            or RunResult(run_id="test-run", status="succeeded", exit_code=0)
        ),
    )
    assert cli.main(["watch", "--workspace", str(workspace)]) == 0
    assert len(calls) == 1
    base, settings, kwargs = calls[0]
    assert base == workspace
    assert settings.sources.public_api.enabled
    assert kwargs["output_formats"] == ("rss", "html")
    assert kwargs["top"] == 20
    assert kwargs["journal_metrics"] == "bundled"


def test_json_executes_but_ai_still_fails_before_pipeline_side_effects(workspace, monkeypatch, capsys):
    credentials(monkeypatch)
    called = []
    monkeypatch.setattr(
        legacy_cli, "run_watch_recorded",
        lambda *args, **kwargs: called.append(1) or RunResult(
            run_id="json-run", status="succeeded", exit_code=0
        ),
    )

    write_config(workspace, formats=("json",))
    assert cli.main(["watch", "--workspace", str(workspace)]) == 0
    assert called == [1]
    called.clear()

    ai = {
        "services": {"s": {"provider": "openrouter", "model": "some-model"}},
        "features": {
            "rerank": {"enabled": False},
            "summary": {"enabled": True, "service": "s"},
        },
    }
    write_config(workspace, formats=("rss",), ai=ai)
    assert cli.main(["watch", "--workspace", str(workspace)]) == 3
    assert "CAPABILITY_UNAVAILABLE" in capsys.readouterr().err
    assert called == []


def test_v2_rejects_legacy_run_overlay_before_pipeline(workspace, monkeypatch, capsys):
    write_config(workspace, formats=("rss",))
    credentials(monkeypatch)
    called = []
    monkeypatch.setattr(legacy_cli, "run_watch", lambda *args, **kwargs: called.append(1))
    assert cli.main(["watch", "--workspace", str(workspace), "--top", "5"]) == 2
    assert "CONFIG_OPTION_UNSUPPORTED" in capsys.readouterr().err
    assert called == []


def test_legacy_and_basic_v2_share_ranking_and_rss_html_content(
    workspace, candidates, monkeypatch
):
    v2_workspace = workspace / "v2-workspace"
    v2_workspace.mkdir()
    write_config(v2_workspace, formats=("rss", "html", "json"))
    identity = pseudonymous_library_identity("user", "123456")
    legacy_state = workspace / "legacy-state"
    v2_state = workspace / "v2-state"
    for state in (legacy_state, v2_state):
        storage = ProfileStorage(state / "profile.sqlite")
        storage.initialize()
        for row in read_json(FIXTURES / "zotero.json"):
            item = ZoteroItem.from_zotero_api(row)
            storage.upsert_item(item, f"hash-{item.key}")
        storage.set_library_identity_sha256(identity)
        storage.set_last_modified_version(10)
        storage.close()

    monkeypatch.setattr(
        ingest_zotero_api.ZoteroIngestor,
        "run",
        lambda *args, **kwargs: SimpleNamespace(fetched=0, updated=0, removed=0),
    )
    monkeypatch.setattr(build_profile, "TextVectorizer", FixedVectors)
    monkeypatch.setattr(score_rank, "TextVectorizer", FixedVectors)
    monkeypatch.setattr(
        fetch_new.CandidateFetcher,
        "fetch_with_outcome",
        lambda self: fetch_new.CandidateFetchOutcome(candidates, "succeeded", False, True),
    )
    captures = []
    original_rank = score_rank.WorkRanker.rank

    def capture(self, values):
        result = original_rank(self, values)
        captures.append([item.model_dump(mode="json") for item in result])
        return result

    monkeypatch.setattr(score_rank.WorkRanker, "rank", capture)
    legacy_reports = workspace / "legacy-reports"
    v2_reports = workspace / "v2-reports"

    legacy_cli.main([
        "profile", "--workspace", str(workspace), "--state-dir", str(legacy_state)
    ])
    legacy_cli.main([
        "watch", "--workspace", str(workspace), "--state-dir", str(legacy_state),
        "--reports-dir", str(legacy_reports), "--rss", "--report", "--top", "20",
        "--journal-metrics", "bundled",
    ])
    assert cli.main([
        "profile", "--workspace", str(v2_workspace), "--state-dir", str(v2_state)
    ]) == 0
    assert cli.main([
        "watch", "--workspace", str(v2_workspace), "--state-dir", str(v2_state),
        "--reports-dir", str(v2_reports),
    ]) == 0

    assert captures[0] == captures[1]
    assert (legacy_reports / "feed.xml").read_bytes() == (v2_reports / "feed.xml").read_bytes()
    assert (legacy_reports / "report-20260114.html").read_bytes() == (
        v2_reports / "report.html"
    ).read_bytes()
    recommendations = read_json(v2_reports / "recommendations.json")
    assert recommendations["schema_name"] == "zotwatch-recommendations"
    assert recommendations["schema_version"] == 1
    assert len(recommendations["recommendations"]) == 4
    pointer = read_json(v2_reports / ".zotwatch-output/latest-success.json")
    assert pointer["generation_id"]
    assert all(
        item["path"].startswith(".zotwatch-output/generations/")
        for item in pointer["artifacts"]
    )
