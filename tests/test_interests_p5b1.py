from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from src.models import CandidateWork
from zotwatch.interests.contract import PROFILE_PATH, Profile, InterestError, blob_sha, parse_profile
from zotwatch.interests.git import read_local, readiness
from zotwatch.interests.ranking import rank, encode_texts, semantic_input_hash
from zotwatch.interests.results import TopicRunResult, materialize
from zotwatch.interests import runner
from zotwatch.paths import RuntimePaths
from zotwatch.runtime import load_effective_runtime
from .helpers import ROOT, FIXTURES, FixedVectors
from .test_config_v2 import minimal_config


def profile():
    return json.loads((FIXTURES / "p5b1/confirmed-profile.json").read_text())


def git(root, *args, input=None):
    return subprocess.run(["git", "-C", str(root), *args], input=input, text=True,
                          capture_output=True, check=True).stdout.strip()


def commit_profile(root, value):
    if not (root / ".git").exists():
        git(root, "init", "-q", "-b", "zotwatch-feedback")
        git(root, "config", "user.name", "Synthetic Test")
        git(root, "config", "user.email", "synthetic@example.invalid")
    path = root / PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    path.write_text(content)
    git(root, "add", PROFILE_PATH)
    git(root, "commit", "-qm", "Confirm synthetic interests")
    return git(root, "rev-parse", "HEAD"), blob_sha(content.encode())


def candidate(key="paper", **kwargs):
    return CandidateWork(source="crossref", identifier=key, title=key,
                         authors=["Preferred Author"], venue="Preferred Venue", **kwargs)


def test_shared_schema_and_fixture_are_byte_identical():
    web = ROOT.parent / "zotwatch-web/contracts/interests"
    assert (ROOT / "zotwatch/resources/interest-profile-v1.schema.json").read_bytes() == (web / "interest-profile-v1.schema.json").read_bytes()
    assert (FIXTURES / "p5b1/confirmed-profile.json").read_bytes() == (web / "confirmed-profile.json").read_bytes()
    assert json.loads((web / "interest-profile-v1.schema.json").read_text()) == {
        **Profile.model_json_schema(), "$schema": "https://json-schema.org/draft/2020-12/schema"}


@pytest.mark.parametrize("change", [
    lambda v: v.update(schema_version=2), lambda v: v.update(schema_version=True),
    lambda v: v.update(unknown="private"), lambda v: v["interests"].append(v["interests"][0]),
    lambda v: v["interests"][0].update(description=" "),
    lambda v: v["interests"][0].update(name="a" * 81),
    lambda v: v["confirmation"].update(confirmed_at="2026-02-30T00:00:00Z"),
])
def test_closed_schema_rejects_bad_state_without_echoing_content(change):
    value = profile(); change(value)
    with pytest.raises(InterestError, match="^INTEREST_INVALID$"):
        parse_profile(json.dumps(value).encode())


def test_duplicate_json_keys_rejected():
    with pytest.raises(InterestError):
        parse_profile(b'{"schema_version":1,"schema_version":2}')


def test_real_git_revision_operations_and_rebuild_preservation(tmp_path):
    value = profile()
    commit, sha = commit_profile(tmp_path, value)
    first = read_local(tmp_path)
    assert (first.feedback_commit_sha, first.profile_blob_sha) == (commit, sha)
    assert readiness(first, 123, "12345") == "ready"
    value["interests"][0]["name"] = "Renamed"
    commit_profile(tmp_path, value)
    renamed = read_local(tmp_path)
    assert renamed.profile.interests[0].id == first.profile.interests[0].id
    assert semantic_input_hash(renamed.profile) == semantic_input_hash(first.profile)
    assert renamed.profile_blob_sha != first.profile_blob_sha
    value["interests"][0]["status"] = "muted"
    commit_profile(tmp_path, value)
    assert readiness(read_local(tmp_path), 123, "12345") == "paused"
    value["interests"][0]["status"] = "active"
    commit_profile(tmp_path, value)
    assert readiness(read_local(tmp_path), 123, "12345") == "ready"
    value["interests"] = []
    commit_profile(tmp_path, value)
    assert readiness(read_local(tmp_path), 123, "12345") == "paused"
    data = tmp_path / "data"; data.mkdir(); (data / "old-checkpoint").write_text("stale")
    before = git(tmp_path, "rev-parse", "zotwatch-feedback")
    shutil.rmtree(data)
    assert git(tmp_path, "rev-parse", "zotwatch-feedback") == before
    assert first.profile.interests[0].name == "Graph learning"  # fixed in-memory snapshot


def test_missing_and_wrong_identity_fail_safely(tmp_path):
    assert readiness(read_local(tmp_path), 123, "12345") == "not_ready"
    commit_profile(tmp_path, profile())
    for repo, library, code in [(124, "12345", "REPOSITORY"), (123, "999", "LIBRARY")]:
        with pytest.raises(InterestError, match=code): readiness(read_local(tmp_path), repo, library)


def test_git_symlink_is_not_a_profile(tmp_path):
    commit_profile(tmp_path, profile())
    path = tmp_path / PROFILE_PATH; path.unlink(); path.symlink_to("other.json")
    git(tmp_path, "add", PROFILE_PATH); git(tmp_path, "commit", "-qm", "synthetic symlink")
    with pytest.raises(InterestError, match="GIT_INVALID"): read_local(tmp_path)


@pytest.mark.parametrize("similarity, matched", [(0.349999, False), (0.35, True), (-0.5, False)])
def test_threshold_precedes_boost_and_bonuses(similarity, matched):
    value = profile(); value["interests"][0].update(priority="high", horizon="short_term")
    def encode(_, texts):
        return np.asarray([[1, 0]] if texts[0].startswith("Molecular") else [[similarity, math.sqrt(1-similarity**2)]])
    result = rank(Profile.model_validate(value), [candidate()], None, encode=encode)
    assert bool(result) is matched
    if matched:
        assert result[0]["preference_bonus"] == 0.05
        assert result[0]["score"] == pytest.approx(similarity * 1.2 * 1.1 + 0.05)
        assert not any(k in result[0] for k in ("citations", "recency", "journal_quality"))


def test_max_not_sum_muted_and_deterministic_match():
    value = profile()
    second = deepcopy(value["interests"][0]); second["id"] = "33333333-3333-4333-8333-333333333333"
    value["interests"].append(second)
    def encode(_, texts): return np.asarray([[1.0, 0.0]] * len(texts))
    results = rank(Profile.model_validate(value), [candidate()], None, encode=encode)
    assert results[0]["score"] == 1.05
    assert results[0]["matched_interest_id"] == value["interests"][0]["id"]
    value["interests"][0]["status"] = "muted"
    assert rank(Profile.model_validate(value), [candidate()], None, encode=encode)[0]["matched_interest_id"] == second["id"]
    value["interests"][1]["status"] = "muted"
    assert rank(Profile.model_validate(value), [candidate()], None, encode=encode) == []


def test_token_windows_consume_tail_without_names_or_authors():
    calls = []
    tokenizer = SimpleNamespace(num_special_tokens_to_add=lambda: 0,
        encode=lambda s, **kwargs: list(s), decode=lambda v, **kwargs: "".join(v))
    vectorizer = SimpleNamespace(model=SimpleNamespace(tokenizer=tokenizer, max_seq_length=4),
        encode=lambda texts: calls.extend(texts) or np.array([[1., 0.]] * len(texts)))
    assert np.allclose(encode_texts(vectorizer, ["abcdefghij"]), [[1, 0]])
    assert calls == ["abcd", "efgh", "ij"]


def setup_topic(tmp_path, monkeypatch):
    value = profile(); value["library_scope"]["id"] = "123456"
    worker_git = os.getenv("P5B1_WORKER_GIT")
    if worker_git:
        shutil.copytree(Path(worker_git) / ".git", tmp_path / ".git")
        git(tmp_path, "symbolic-ref", "HEAD", "refs/heads/zotwatch-feedback")
        git(tmp_path, "config", "user.name", "Synthetic Test")
        git(tmp_path, "config", "user.email", "synthetic@example.invalid")
        monkeypatch.setenv("ZOTERO_USER_ID", read_local(tmp_path).profile.library_scope.id)
    else:
        commit_profile(tmp_path, value)
    config = deepcopy(minimal_config()); config["ranking"]["policy"] = "topic-v1"
    config["outputs"] = {"formats": ["json"], "publish": False}
    (tmp_path / "zotwatch.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setenv("ZOTWATCH_WORKSPACE_REPOSITORY_ID", "123")
    return RuntimePaths.resolve(workspace=tmp_path), load_effective_runtime(tmp_path)


def test_not_ready_and_paused_do_not_fetch_candidates(tmp_path, monkeypatch):
    paths, effective = setup_topic(tmp_path, monkeypatch)
    args = SimpleNamespace(command="watch", full=False, weekly=False)
    absent = runner.run(args, paths, effective, snapshot_loader=lambda _: None)
    assert (absent.status, absent.exit_code, absent.recommendations) == ("not_ready", 0, [])
    v = profile(); v["library_scope"]["id"] = "123456"; v["interests"][0]["status"] = "muted"
    commit_profile(tmp_path, v)
    paused = runner.run(args, paths, effective)
    assert (paused.status, paused.exit_code) == ("paused", 0)
    assert not (paths.state / "profile.sqlite").exists()


def test_actual_pipeline_consumes_exact_revision_through_rebuild_and_materialization(tmp_path, monkeypatch):
    from src import cli as engine
    from src.models import ZoteroItem
    from .helpers import read_json
    paths, effective = setup_topic(tmp_path, monkeypatch)
    original = read_local(tmp_path)
    def ingest(self, **kwargs):
        self.storage.initialize()
        for row in read_json(FIXTURES / "zotero.json"):
            item = ZoteroItem.from_zotero_api(row); self.storage.upsert_item(item, item.key)
        self.storage.set_library_identity_sha256(kwargs["library_identity_sha256"])
        self.storage.set_last_modified_version(10)
    monkeypatch.setattr(engine.ZoteroIngestor, "run", ingest)
    monkeypatch.setattr(engine.build_profile_module, "TextVectorizer", FixedVectors)
    works = [candidate("fresh unseen", published=datetime(2026, 1, 15, tzinfo=timezone.utc))]
    monkeypatch.setattr(engine.CandidateFetcher, "fetch_with_outcome", lambda self: SimpleNamespace(status="succeeded", candidates=works))
    observed = []
    def ranking(profile, candidates, vectorizer):
        observed.append(profile.interests[0].description)
        return rank(profile, candidates, vectorizer, encode=lambda _, texts: np.asarray([[1., 0.]] * len(texts)))
    args = SimpleNamespace(command="watch", full=True, weekly=False)
    first = runner.run(args, paths, effective, ranker=ranking)
    assert first.status == "succeeded", first
    assert first.evidence.profile_blob_sha == original.profile_blob_sha
    assert first.evidence.feedback_commit_sha == original.feedback_commit_sha
    assert len(first.recommendations) == 1
    machine = tmp_path / "machine.json"; machine.write_text(first.model_dump_json()+"\n")
    assert materialize(machine, 0, paths.state, tmp_path / "private").run_id == first.run_id
    shutil.rmtree(paths.state)
    second = runner.run(args, paths, effective, ranker=ranking)
    assert second.status == "succeeded"
    assert second.evidence.profile_blob_sha == first.evidence.profile_blob_sha
    assert observed == [original.profile.interests[0].description] * 2
    assert read_local(tmp_path).feedback_commit_sha == original.feedback_commit_sha
    changed = profile(); changed["library_scope"]["id"] = original.profile.library_scope.id; changed["interests"][0]["description"] = "User correction"
    commit_profile(tmp_path, changed)
    # An already captured snapshot still consumes its original revision.
    old = runner.run(args, paths, effective, snapshot_loader=lambda _: original, ranker=ranking)
    assert old.evidence.profile_blob_sha == original.profile_blob_sha
    newest = runner.run(args, paths, effective, ranker=ranking)
    assert newest.evidence.profile_blob_sha != old.evidence.profile_blob_sha
    assert observed[-2:] == [original.profile.interests[0].description, "User correction"]


def test_result_rejects_fake_scores_and_skipped_recommendations():
    from zotwatch.interests.results import TopicRecommendation
    item = rank(Profile.model_validate(profile()), [candidate()], None,
                encode=lambda _, texts: np.asarray([[1., 0.]] * len(texts)))[0]
    TopicRecommendation.model_validate(item)
    item["score"] = 99.0
    with pytest.raises(ValueError): TopicRecommendation.model_validate(item)
    item["score"] = float("nan")
    with pytest.raises(ValueError): TopicRecommendation.model_validate(item)


def test_remote_snapshot_uses_one_commit_and_rejects_symlinks_and_forbidden():
    from zotwatch.interests.git import read_github
    import base64
    content = (FIXTURES / "p5b1/confirmed-profile.json").read_bytes()
    sha = blob_sha(content)
    seen = []
    class Response:
        status_code = 200
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield json.dumps(self.data).encode()
    class Client:
        mode = "100644"
        forbidden = False
        def get(self, url, **kwargs):
            seen.append(url)
            assert kwargs["allow_redirects"] is False
            assert kwargs["headers"]["Authorization"] == "Bearer synthetic"
            path = url.removeprefix("https://api.github.com/repos/test/private")
            if path == "": data = {"id": 123, "private": True}
            elif path == "/git/ref/heads/zotwatch-feedback": data = {"object": {"type": "commit", "sha": "a" * 40}}
            elif path == "/git/commits/" + "a" * 40: data = {"sha": "a"*40, "tree": {"sha": "b"*40}}
            elif path.startswith("/git/trees/"):
                current = path.rsplit("/", 1)[1]
                index = ["b"*40, "c"*40, "d"*40, "e"*40].index(current)
                data = {"sha": current, "tree": [{"path": PROFILE_PATH.split("/")[index],
                    "type": "blob" if index == 3 else "tree", "mode": self.mode if index == 3 else "040000",
                    "sha": sha if index == 3 else ["c"*40, "d"*40, "e"*40][index]}]}
            elif path == "/git/blobs/" + sha: data = {"sha": sha, "encoding": "base64", "size": len(content), "content": base64.b64encode(content).decode()}
            else: raise AssertionError(path)
            response = Response(data)
            if self.forbidden: response.status_code = 403
            return response
    client = Client()
    value = read_github("test/private", 123, "synthetic", session=client)
    assert value.profile_blob_sha == sha and value.feedback_commit_sha == "a"*40
    assert sum("/git/ref/" in u for u in seen) == 1
    client.mode = "120000"
    with pytest.raises(InterestError, match="GIT_INVALID"): read_github("test/private", 123, "synthetic", session=client)
    client.forbidden = True
    with pytest.raises(InterestError, match="UNAVAILABLE"): read_github("test/private", 123, "synthetic", session=client)


def test_workflow_v2_skipped_result_is_private_and_sealed(tmp_path, monkeypatch, capsys):
    from zotwatch.workflow.cli import main
    paths, effective = setup_topic(tmp_path, monkeypatch)
    value = runner.run(SimpleNamespace(command="watch", full=False, weekly=False), paths, effective, snapshot_loader=lambda _: None)
    machine = tmp_path / "machine.json"; machine.write_text(value.model_dump_json()+"\n")
    private = tmp_path / "private"
    assert main(["result", "--machine-result", str(machine), "--process-exit-code", "0", "--state", str(paths.state),
                 "--reports", str(paths.reports), "--publishable", str(tmp_path / "public"), "--private", str(private / "final"),
                 "--summary-output", str(tmp_path / "summary.json")]) == 0
    assert not (tmp_path / "public").exists()
    assert main(["seal-result", "--private", str(private), "--engine-sha", "a"*40, "--repository", "test/private",
                 "--repository-id", "123", "--caller-run-id", "456", "--run-id", value.run_id,
                 "--status", "not_ready", "--publish-requested", "false"]) == 0
    envelope = json.loads((private / "topic-workflow-envelope-v2.json").read_text())
    assert envelope["result_status"] == "not_ready" and envelope["schema_version"] == 2
    assert not (private / "workflow-envelope.json").exists()


def test_topic_requires_private_json_output(tmp_path, monkeypatch):
    from zotwatch.config import ConfigError
    setup_topic(tmp_path, monkeypatch)
    config = yaml.safe_load((tmp_path / "zotwatch.yaml").read_text())
    config["outputs"]["publish"] = True
    (tmp_path / "zotwatch.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(ConfigError): load_effective_runtime(tmp_path)


def test_future_state_and_legacy_policy_do_not_run(tmp_path, monkeypatch, capsys):
    from zotwatch import cli
    paths, effective = setup_topic(tmp_path, monkeypatch)
    config = yaml.safe_load((tmp_path / "zotwatch.yaml").read_text()); config["ranking"]["policy"] = "legacy-v1"
    (tmp_path / "zotwatch.yaml").write_text(yaml.safe_dump(config))
    assert cli.main(["watch", "--workspace", str(tmp_path), "--machine-result"]) == 2
    assert "INTEREST_POLICY_MISMATCH" in capsys.readouterr().out
    value = profile(); value["schema_version"] = 9; commit_profile(tmp_path, value)
    result = runner.run(SimpleNamespace(command="watch", full=False, weekly=False), paths, effective)
    assert result.status == "failed" and result.reason == "INTEREST_INVALID"
