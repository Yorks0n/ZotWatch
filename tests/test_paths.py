import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from dotenv.main import load_dotenv

from src import build_profile, cli, fetch_new, ingest_zotero_api, score_rank
from zotwatch.paths import RuntimePaths
from .helpers import FIXTURES, FixedVectors, Response, read_json


def test_path_defaults_are_current_workspace(workspace):
    paths = RuntimePaths.resolve()
    assert paths.workspace == workspace
    assert paths.state == workspace / "data"
    assert paths.reports == workspace / "reports"
    assert not (workspace / "data").exists()  # Resolving is read-only.


def test_path_aliases_and_relative_overrides(workspace):
    paths = RuntimePaths.resolve(workspace="personal", base_dir="./personal",
                                 state_dir="../state", reports_dir="out")
    assert paths.workspace == workspace / "personal"
    assert paths.state == workspace / "state"
    assert paths.reports == workspace / "personal/out"
    absolute = RuntimePaths.resolve(workspace="personal", state_dir=workspace / "state")
    assert absolute.state == paths.state


def test_conflicting_aliases_fail_before_io(workspace):
    with pytest.raises(SystemExit) as exc:
        cli.main(["profile", "--workspace", "one", "--base-dir", "two"])
    assert exc.value.code == 2
    assert not (workspace / "one").exists()


def test_dotenv_is_limited_to_workspace_and_env_still_wins(workspace, monkeypatch):
    (workspace / ".env").write_text("E1_LOCAL=workspace\nE1_OVERRIDE=file\n")
    parent = workspace / "parent"
    parent.mkdir()
    (parent / ".env").write_text("E1_PARENT=should-not-load\n")
    child = parent / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    monkeypatch.delenv("E1_LOCAL", raising=False)
    monkeypatch.delenv("E1_PARENT", raising=False)
    monkeypatch.setenv("E1_OVERRIDE", "environment")
    monkeypatch.setattr(cli, "load_dotenv", load_dotenv)
    observed = []
    monkeypatch.setattr(cli, "run_profile", lambda *a, **kw: observed.append(
        (os.getenv("E1_LOCAL"), os.getenv("E1_OVERRIDE"), os.getenv("E1_PARENT"))))
    cli.main(["profile", "--workspace", str(workspace)])
    assert observed == [("workspace", "environment", None)]


@pytest.mark.parametrize("relative", [True, False])
def test_real_pipeline_uses_separate_state_and_reports(workspace, candidates, monkeypatch, relative):
    state = workspace.parent / (workspace.name + "-state")
    reports = workspace.parent / (workspace.name + "-reports")
    state_arg = os.path.relpath(state, workspace) if relative else str(state)
    reports_arg = os.path.relpath(reports, workspace) if relative else str(reports)
    rows = read_json(FIXTURES / "zotero.json")
    replies = iter([Response(rows, headers={"Last-Modified-Version": "10"}),
                    Response(status=304), Response({"items": []})])
    monkeypatch.setattr(ingest_zotero_api, "request_with_retry", lambda *a, **kw: next(replies))
    monkeypatch.setattr(build_profile, "TextVectorizer", FixedVectors)
    monkeypatch.setattr(score_rank, "TextVectorizer", FixedVectors)
    monkeypatch.setattr(fetch_new.CandidateFetcher, "_fetch_public_candidates", lambda *a: candidates)
    monkeypatch.setattr(fetch_new.CandidateFetcher, "_fetch_crossref_top_venues", lambda *a: [])
    common = ["--workspace", str(workspace), "--state-dir", state_arg]
    cli.main(["profile", "--full", *common])
    cli.main(["watch", "--rss", "--report", "--top", "3", "--reports-dir", reports_arg, *common])
    assert {p.name for p in state.iterdir()} == {
        ".zotwatch-state.lock",
        "profile.sqlite",
        "computational",
        "cache",
    }
    assert (state / "computational/current.json").is_file()
    assert (state / "cache/candidate_cache.json").exists()
    assert {p.name for p in reports.iterdir()} == {"feed.xml", "report-20260114.html"}
    assert [node.text for node in ET.parse(reports / "feed.xml").findall("channel/item/guid")] == ["alpha", "beta", "gamma"]
    assert not (workspace / "data").exists()
    assert not (workspace / "reports").exists()
