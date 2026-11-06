import importlib.util
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError

import pytest

from zotwatch.workflow.daily_cache import snapshot, restore
from .test_latent_lifecycle_p5c4 import store, ensure, NOW, IDENTITY
from zotwatch.interests.lifecycle import LifecycleStore
from src.storage import ProfileStorage


def database(root):
    root.mkdir()
    with sqlite3.connect(root / "profile.sqlite") as db:
        db.execute("CREATE TABLE items (title TEXT)")
        db.execute("INSERT INTO items VALUES ('private library record')")


def test_latest_snapshot_excludes_runs_credentials_and_old_models(tmp_path):
    state, cache = tmp_path / "state", tmp_path / "cache"
    database(state)
    for name in ("runs/old.json", ".env", "optional-ai-cache-v1/provider-secret.json",
                 "latent-state-v1/generations/old/model.json"):
        path = state / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private")
    (state / "center-profiles-v1.json").write_text('{"current":"portrait"}')
    snapshot(state, cache, "42", "123")
    assert {p.name for p in cache.iterdir()} == {"profile.sqlite", "cache.json", "center-profiles-v1.json"}
    restored = tmp_path / "restored"
    assert restore(cache, restored, "42", "123")
    with sqlite3.connect(restored / "profile.sqlite") as db:
        assert db.execute("SELECT title FROM items").fetchone() == ("private library record",)
    assert not restore(cache, restored, "42", "other-library")
    assert list(restored.iterdir()) == []
    (cache / "profile.sqlite").write_bytes(b"broken database")
    assert not restore(cache, restored, "42", "123")


def test_restored_latest_model_reuses_vectors_without_keeping_old_generations(store, tmp_path):
    initial, _, _ = ensure(store)
    current, _, _ = ensure(store, manual=True)
    cache = tmp_path / "latest-cache"
    snapshot(tmp_path, cache, "42", "123456")
    assert [p.name for p in (cache / "latent-state-v1/generations").iterdir()] == [current.interest_model_revision]
    assert initial.interest_model_revision != current.interest_model_revision
    restored = tmp_path / "next-job"
    assert restore(cache, restored, "42", "123456")
    storage = ProfileStorage(restored / "profile.sqlite")
    try:
        deployment, reason, encoded = LifecycleStore(restored).ensure(storage, 42, IDENTITY, "unused", now=NOW)
        assert deployment.interest_model_revision == current.interest_model_revision
        assert encoded == 0 and reason.startswith("reuse_")
    finally:
        storage.close()


def pages_module():
    import sys
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("daily_pages", scripts / "daily_pages.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_publication_has_only_public_pages_and_keeps_rss_history(tmp_path, monkeypatch):
    pages = pages_module()
    result, ai = tmp_path / "result.json", tmp_path / "no-ai.json"
    result.write_text(json.dumps({"schema_name":"zotwatch-latent-auto-run-result", "run_id":"run", "exit_code":0,
        "status":"succeeded", "command":"watch", "recommendations":[{"work_key":"10.1234/a","title":"Public paper"}],
        "evidence":{"private":"must never be published"}}))
    monkeypatch.setattr(pages.rss, "request", lambda *a, **k: (_ for _ in ()).throw(HTTPError(a[0],404,"missing",None,None)))
    monkeypatch.setattr(pages.rss, "public_metadata", lambda _: {"abstract":"public abstract", "journal":"Journal"})
    # merge_feed's resolver is an existing default argument, replace only in this test.
    merge = pages.rss.merge_feed
    monkeypatch.setattr(pages.rss,"merge_feed",lambda final,old,url: merge(final,old,url,resolve=lambda _: {"abstract":"public abstract","journal":"Journal"}))
    output = tmp_path / "public"
    report = pages.prepare(result, ai, output, "https://owner.github.io/results/feed.xml")
    assert report["items"] == 1
    assert {p.name for p in output.iterdir()} == {"feed.xml","feed.xsl","index.html"}
    assert all(b"must never be published" not in p.read_bytes() for p in output.iterdir())
    prior = (output / "feed.xml").read_bytes()
    monkeypatch.setattr(pages.rss,"request",lambda *a,**k:prior)
    pages.prepare(result, ai, tmp_path / "repeat", "https://owner.github.io/results/feed.xml")
    assert (tmp_path / "repeat/feed.xml").read_bytes() == prior
    monkeypatch.setattr(pages.rss,"request",lambda *a,**k: (_ for _ in ()).throw(HTTPError(a[0],503,"outage",None,None)))
    with pytest.raises(HTTPError):
        pages.prepare(result,ai,tmp_path / "failed","https://owner.github.io/results/feed.xml")
    assert not (tmp_path / "failed").exists()
