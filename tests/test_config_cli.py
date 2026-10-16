from datetime import timedelta
from types import SimpleNamespace

import pytest
import yaml

from src import cli
from src.models import RankedWork
from src.settings import load_settings, _expand_env_vars, _load_yaml
from .helpers import ROOT, NOW, ids


def test_old_config_values_and_expansion(settings, monkeypatch):
    assert settings.zotero.api.user_id == "123456"
    assert settings.sources.window_days == 7
    assert settings.sources.arxiv.categories == ["q-bio.GN", "cs.LG"]
    assert sum(settings.scoring.weights.model_dump().values()) == pytest.approx(.95)
    assert settings.scoring.thresholds.model_dump() == {"must_read": .75, "consider": .5}
    assert settings.scoring.decay_days == {"fast": 3, "medium": 7, "slow": 30}
    assert settings.scoring.whitelist_authors == settings.scoring.whitelist_venues == []
    monkeypatch.setenv("E0_LABEL", "synthetic")
    assert _expand_env_vars({"list": ["${E0_LABEL}", "$E0_MISSING", 3]}) == {"list": ["synthetic", "$E0_MISSING", 3]}


def test_bug_config_key_priority_and_mailto(settings, monkeypatch):
    # BUG-C1/C2: explicit YAML wins over env; MAILTO env is not consulted.
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "synthetic-env-key")
    monkeypatch.setenv("CROSSREF_MAILTO", "override@example.invalid")
    monkeypatch.setenv("OPENALEX_MAILTO", "override@example.invalid")
    assert settings.sources.public_api.api_key() == "synthetic-public-key"
    assert settings.sources.crossref.mailto == settings.sources.openalex.mailto == "you@example.com"
    settings.sources.public_api.publishable_key = None
    assert settings.sources.public_api.api_key() == "synthetic-env-key"


@pytest.mark.parametrize("flags,full", [([], False), (["--full"], True), (["--weekly"], True)])
def test_profile_cli_flags(workspace, monkeypatch, flags, full):
    calls = []
    monkeypatch.setattr(cli, "run_profile", lambda base, cfg, db, **kw: calls.append((base, kw)))
    assert cli.main(["profile", "--base-dir", str(workspace), *flags, "--verbose"]) is None
    assert calls == [(workspace, {"full": full})]


def test_watch_cli_flags(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "run_watch", lambda base, cfg, db, **kw: calls.append(kw))
    cli.main(["watch", "--base-dir", str(workspace)])
    cli.main(["watch", "--base-dir", str(workspace), "--rss", "--report", "--push", "--top", "3"])
    assert calls == [dict(rss=False, report=False, top=50, push=False), dict(rss=True, report=True, top=3, push=True)]


@pytest.mark.parametrize("args", [[], ["unknown"], ["watch", "--top", "bad"]])
def test_parser_exits_two(args):
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2


def test_configuration_and_key_failures(workspace, monkeypatch):
    monkeypatch.delenv("ZOTERO_API_KEY")
    with pytest.raises(RuntimeError, match="ZOTERO_API_KEY"):
        cli.main(["profile", "--base-dir", str(workspace)])
    (workspace / "config/zotero.yaml").unlink()
    with pytest.raises(FileNotFoundError, match="zotero.yaml"):
        cli.main(["watch", "--base-dir", str(workspace)])
    bad = workspace / "bad.yaml"
    bad.write_text("- sequence\n")
    with pytest.raises(ValueError, match="mapping"):
        _load_yaml(bad)


def ranked(identifier, source="crossref", published=NOW):
    return RankedWork(source=source, identifier=identifier, title=identifier, published=published,
                      score=.1, similarity=0, recency_score=1, metric_score=0,
                      author_bonus=0, venue_bonus=0, label="ignore")


def test_bug_preprint_prefix_cap_and_source_classification():
    works = [ranked("early", "arxiv"), ranked("a"), ranked("b"), ranked("c"),
             ranked("late", "biorxiv"), ranked("posted", "crossref")]
    works[-1].extra = {"is_preprint": True, "candidate_type": "posted-content"}
    assert ids(cli._limit_preprints(works, max_ratio=.3)) == ["a", "b", "c", "late", "posted"]
    assert cli._limit_preprints(works, max_ratio=0) == works


def test_seven_day_boundary():
    works = [ranked("edge", published=NOW-timedelta(days=7)),
             ranked("old", published=NOW-timedelta(days=7, microseconds=1)),
             ranked("missing", published=None), ranked("future", published=NOW+timedelta(days=1))]
    assert ids(cli._filter_recent(works, days=7)) == ["edge", "future"]
    assert cli._filter_recent(works, days=0) == works


@pytest.mark.parametrize("empty", [False, True])
def test_watch_ensures_state_and_keeps_legacy_filters(workspace, settings, storage, monkeypatch, empty):
    events = []
    items = [] if empty else [ranked("first", published=NOW-timedelta(days=2)), ranked("second"),
                              ranked("old", published=NOW-timedelta(days=8))]
    settings.sources.window_days = 30  # CLI still uses hard-coded seven days.
    monkeypatch.setattr(
        cli,
        "ZoteroIngestor",
        lambda *a: SimpleNamespace(run=lambda **kw: events.append(("ingest", kw["full"]))),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_computational_state",
        lambda *a, **kw: events.append(("ensure", kw["force"]))
        or (SimpleNamespace(profile={}, generation_id="synthetic"), None),
    )
    monkeypatch.setattr(cli, "CandidateFetcher", lambda *a, **kw: SimpleNamespace(fetch_all=lambda: items))
    monkeypatch.setattr(cli, "DedupeEngine", lambda *a: SimpleNamespace(filter=lambda works: works))
    monkeypatch.setattr(cli, "WorkRanker", lambda *a, **kw: SimpleNamespace(rank=lambda works: works))
    monkeypatch.setattr(cli, "ZoteroPusher", lambda *a: SimpleNamespace(push=lambda works: events.append(("push", ids(works)))))
    cli.run_watch(workspace, settings, storage, rss=True, report=True, top=1, push=True)
    assert events == [("ingest", False), ("ensure", False)] + ([] if empty else [("push", ["first"])])
    filename = "report-empty.html" if empty else "report-20260113.html"
    text = (workspace / "reports" / filename).read_text()
    if not empty:
        assert "Label: ignore" in text
        assert "second" not in text
    assert (workspace / "reports/feed.xml").exists()


def test_original_daily_workflow_contract():
    workflow = yaml.load((ROOT / ".github/workflows/daily_watch.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [{"cron": "5 22 * * *"}]
    assert workflow["on"]["push"]["branches"] == ["main", "codex/dev"]
    assert "workflow_dispatch" in workflow["on"]
    job = workflow["jobs"]["watch"]
    assert job["permissions"] == dict(contents="write", pages="write", **{"id-token": "write"})
    steps = job["steps"]
    cache = next(step for step in steps if step.get("id") == "cache-profile")
    assert cache["with"]["path"].splitlines() == ["data/profile.sqlite", "data/faiss.index", "data/profile.json"]
    assert cache["with"]["key"] == "profile-${{ env.CACHE_KEY }}"
    profile = next(step for step in steps if step.get("name") == "Build Zotero profile")
    assert profile["if"] == "steps.cache-profile.outputs.cache-hit != 'true'"
    assert profile["run"] == "python -m src.cli profile --full"
    assert any(step.get("run") == "python -m src.cli watch --rss --top 20" for step in steps)
    assert next(step for step in steps if "setup-python" in step.get("uses", ""))["with"]["python-version"] == "3.11"
