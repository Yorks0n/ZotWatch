from datetime import datetime, timezone
import json
from types import SimpleNamespace

import numpy as np
import pytest

from src import cli as engine
from src.models import ZoteroItem
from zotwatch.interests import runner, recall_integration
from zotwatch.interests.center_recall_contract import save_center_model
from zotwatch.interests.git import read_local
from zotwatch.interests.integration_results import IntegrationRunResult
from zotwatch.interests.ranking import rank
from .helpers import FixedVectors, FIXTURES, read_json
from .test_center_recall_p5b4 import model, unit
from .test_interests_p5b1 import setup_topic, candidate, profile, commit_profile


def integrated_args(**kwargs):
    return SimpleNamespace(command="watch", full=True, weekly=False,
        candidate_policy="center-recall-v1", **kwargs)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    paths, effective = setup_topic(tmp_path, monkeypatch)
    m = model(rows=((1., 0.),))
    path = paths.state / "latent/recall/center-recall-v1/model.json"
    path.parent.mkdir(parents=True)
    save_center_model(path, m)
    def ingest(self, **kwargs):
        self.storage.initialize()
        for row in read_json(FIXTURES / "zotero.json"):
            item = ZoteroItem.from_zotero_api(row)
            self.storage.upsert_item(item, item.key)
        self.storage.set_library_identity_sha256(kwargs["library_identity_sha256"])
        self.storage.set_last_modified_version(10)
    monkeypatch.setattr(engine.ZoteroIngestor, "run", ingest)
    monkeypatch.setattr(engine.build_profile_module, "TextVectorizer", FixedVectors)
    return paths, effective, m, path


def works():
    return [candidate(key, abstract="semantic abstract", published=datetime(2026, 1, 15, tzinfo=timezone.utc))
            for key in ("single-cell", "isolated-nn-negative")]


def install_collection(monkeypatch, candidates):
    monkeypatch.setattr(engine.CandidateFetcher, "fetch_with_outcome",
        lambda self: SimpleNamespace(status="succeeded", candidates=candidates))


def install_encoding(monkeypatch, m, rows):
    monkeypatch.setattr(recall_integration, "encode_candidates", lambda loaded, cache, records:
        (unit(rows), {"embedding_text_fingerprint": m.embedding_text_fingerprint}))


def ranking(profile, candidates, vectorizer):
    return rank(profile, candidates, vectorizer,
        encode=lambda _, texts: np.asarray([[1., 0.]] * len(texts)))


def test_collection_recall_ranking_exact_revisions_and_unchanged_scores(pipeline, monkeypatch):
    paths, effective, m, _ = pipeline
    snapshot = read_local(paths.workspace)
    collected = works()
    install_collection(monkeypatch, collected)
    install_encoding(monkeypatch, m, [[.6, .8], [.54, (1-.54**2)**.5]])
    observed = []
    def ranked(profile, candidates, vectorizer):
        observed.extend(c.identifier for c in candidates)
        return ranking(profile, candidates, vectorizer)
    # The real pipeline must not read P5B3 member/cached historical vectors.
    original_load = np.load
    def load(path, *args, **kwargs):
        assert "latent" not in str(path)
        return original_load(path, *args, **kwargs)
    monkeypatch.setattr(np, "load", load)
    result = runner.run(integrated_args(), paths, effective, ranker=ranked)
    assert result.status == "succeeded", result
    assert result.schema_version == 3 and result.candidate_policy == "center-recall-v1"
    assert observed == ["single-cell"]
    assert [r.recalled for r in result.evidence.recall_decisions] == [True, False]
    rec = result.recommendations[0]
    assert rec.latent_recall.interest_model_revision == m.interest_model_revision
    assert rec.latent_recall.best_match.center_cosine == pytest.approx(.6)
    assert rec.latent_recall.best_match.interest_id == m.centers[0].interest_id
    assert rec.confirmed_profile_revision == snapshot.profile_blob_sha
    assert result.evidence.feedback_commit_sha == snapshot.feedback_commit_sha
    baseline = ranking(snapshot.profile, collected[:1], None)[0]
    assert {k: rec.model_dump()[k] for k in baseline} == baseline
    # Serialized, persisted recommendation keeps the existing score arithmetic.
    recorded = paths.state / "runs" / f"topic-{result.run_id}.json"
    assert IntegrationRunResult.model_validate_json(recorded.read_bytes()) == result
    for field in ("confirmed_profile_revision",):
        invalid = result.model_dump()
        invalid["recommendations"][0][field] = "f" * 40
        with pytest.raises(ValueError): IntegrationRunResult.model_validate(invalid)
    invalid = result.model_dump()
    invalid["recommendations"][0]["latent_recall"]["interest_model_revision"] = "f" * 32
    with pytest.raises(ValueError): IntegrationRunResult.model_validate(invalid)


@pytest.mark.parametrize("state", ["absent", "muted", "empty"])
def test_confirmed_readiness_precedes_latent_or_collection(tmp_path, monkeypatch, state):
    paths, effective = setup_topic(tmp_path, monkeypatch)
    loader = None
    if state == "absent":
        loader = lambda _: None
    else:
        value = profile(); value["library_scope"]["id"] = "123456"
        if state == "muted": value["interests"][0]["status"] = "muted"
        else: value["interests"] = []
        commit_profile(tmp_path, value)
    monkeypatch.setattr(recall_integration, "LatentRecallRuntime",
        lambda *a: pytest.fail("readiness must precede latent loading"))
    install_collection(monkeypatch, [])
    result = runner.run(integrated_args(), paths, effective,
                        **({"snapshot_loader": loader} if loader else {}))
    assert result.status == ("not_ready" if state == "absent" else "paused")
    assert result.recommendations == []
    assert not (paths.state / "profile.sqlite").exists()


@pytest.mark.parametrize("failure", ["missing", "corrupt", "threshold", "empty"])
def test_artifact_unavailable_is_not_ready_without_fetch_or_fallback(pipeline, monkeypatch, failure):
    paths, effective, m, path = pipeline
    if failure == "missing": path.unlink()
    elif failure == "corrupt": path.write_text('{"model":{}}')
    else:
        path.unlink()
        save_center_model(path, model(threshold=.6) if failure == "threshold" else model(rows=()))
    monkeypatch.setattr(engine.CandidateFetcher, "fetch_with_outcome", lambda self: pytest.fail("must not fetch"))
    result = runner.run(integrated_args(), paths, effective, ranker=lambda *a: pytest.fail("no fallback"))
    assert (result.status, result.reason) == ("not_ready", "LATENT_RECALL_ARTIFACT_UNAVAILABLE")


@pytest.mark.parametrize("failure", ["fingerprint", "weights", "dimension"])
def test_encoder_incompatibility_stops_revision_before_ranking(pipeline, monkeypatch, failure):
    paths, effective, m, _ = pipeline
    install_collection(monkeypatch, works())
    if failure == "weights":
        # Exercise the actual frozen encoder's on-disk checksum guard.
        cache = paths.state / "encoder-cache"
        weights = cache / ("models--" + m.encoder.model_identifier.replace("/", "--")) / "snapshots" / m.encoder.model_revision / "model.safetensors"
        weights.parent.mkdir(parents=True); weights.write_bytes(b"incompatible weights")
        args = integrated_args(latent_encoder_cache=cache)
    else:
        monkeypatch.setattr(recall_integration, "encode_candidates", lambda *a:
            (unit([[1.,0.],[1.,0.]]) if failure == "fingerprint" else np.ones((2,3)),
             {"embedding_text_fingerprint": "f"*64 if failure == "fingerprint" else m.embedding_text_fingerprint}))
        args = integrated_args()
    result = runner.run(args, paths, effective, ranker=lambda *a: pytest.fail("mismatch cannot rank"))
    assert (result.status, result.reason) == ("not_ready", "LATENT_RECALL_ENCODER_UNAVAILABLE")
    assert result.evidence.latent_recall.interest_model_revision == m.interest_model_revision
    assert result.recommendations == []


def test_missing_abstract_duplicate_and_empty_pool(pipeline, monkeypatch):
    paths, effective, m, path = pipeline
    collected = works(); collected[1].abstract = None
    install_collection(monkeypatch, collected + collected[:1])
    install_encoding(monkeypatch, m, [[1., 0.]])
    result = runner.run(integrated_args(), paths, effective, ranker=ranking)
    assert result.status == "succeeded"
    assert len(result.evidence.recall_decisions) == 2
    assert result.evidence.recall_decisions[1].qualification == "insufficient_semantic_text"
    install_collection(monkeypatch, [])
    install_encoding(monkeypatch, m, [])
    empty = runner.run(integrated_args(), paths, effective, ranker=ranking)
    assert empty.status == "succeeded" and empty.recommendations == []
    assert empty.evidence.latent_recall.interest_model_revision == m.interest_model_revision


def test_profile_snapshot_is_fixed_during_collection_and_muted_topic_never_ranks(pipeline, monkeypatch):
    paths, effective, m, _ = pipeline
    original = read_local(paths.workspace)
    def collect(self):
        changed = profile(); changed["library_scope"]["id"] = "123456"
        changed["interests"][0]["status"] = "muted"
        commit_profile(paths.workspace, changed)
        return SimpleNamespace(status="succeeded", candidates=works())
    monkeypatch.setattr(engine.CandidateFetcher, "fetch_with_outcome", collect)
    install_encoding(monkeypatch, m, [[1.,0.],[0.,1.]])
    result = runner.run(integrated_args(), paths, effective, ranker=ranking)
    assert result.status == "succeeded"
    assert result.recommendations[0].confirmed_profile_revision == original.profile_blob_sha
    assert read_local(paths.workspace).profile_blob_sha != original.profile_blob_sha
    paused = runner.run(integrated_args(), paths, effective, ranker=lambda *a: pytest.fail("muted"))
    assert paused.status == "paused" and paused.recommendations == []


def test_partial_candidates_do_not_release_recommendations(pipeline, monkeypatch):
    paths, effective, _, _ = pipeline
    monkeypatch.setattr(engine.CandidateFetcher, "fetch_with_outcome",
        lambda self: SimpleNamespace(status="degraded", candidates=works()))
    monkeypatch.setattr(recall_integration, "encode_candidates", lambda *a: pytest.fail("partial"))
    result = runner.run(integrated_args(), paths, effective, ranker=lambda *a: pytest.fail("partial"))
    assert result.status == "degraded" and result.recommendations == []


def test_recalled_candidate_cannot_bypass_a_muted_topic(pipeline, monkeypatch):
    paths, effective, m, _ = pipeline
    value = profile(); value["library_scope"]["id"] = "123456"
    muted = {**value["interests"][0], "id": "33333333-3333-4333-8333-333333333333",
             "description": "Muted topic", "status": "muted", "priority": "high", "horizon": "short_term"}
    value["interests"].append(muted)
    commit_profile(paths.workspace, value)
    install_collection(monkeypatch, works()[:1])
    install_encoding(monkeypatch, m, [[1., 0.]])
    texts_seen = []
    def topic_encoding(_, texts):
        texts_seen.extend(texts)
        return np.array([[1., 0.]]) if texts[0].startswith("Molecular") else np.array([[0., 1.]])
    result = runner.run(integrated_args(), paths, effective,
        ranker=lambda p, c, v: rank(p, c, v, encode=topic_encoding))
    assert result.status == "succeeded" and result.evidence.recall_decisions[0].recalled
    assert result.recommendations == []
    assert "Muted topic" not in texts_seen


def test_compatibility_is_explicitly_versioned_and_no_new_result_schema(pipeline, monkeypatch):
    paths, effective, _, path = pipeline
    path.unlink()
    install_collection(monkeypatch, works())
    args = SimpleNamespace(command="watch", full=True, weekly=False,
                           candidate_policy="confirmed-topic-candidates-v1")
    result = runner.run(args, paths, effective, ranker=ranking)
    assert result.schema_version == 2 and result.status == "succeeded"
    assert len(result.recommendations) == 2
    assert "latent_recall" not in result.evidence.model_dump()


def test_cli_opt_in_emits_separate_closed_schema(pipeline, monkeypatch, capsys):
    from zotwatch import cli
    from .helpers import ROOT
    import jsonschema
    paths, effective, m, _ = pipeline
    install_collection(monkeypatch, works())
    install_encoding(monkeypatch, m, [[1., 0.], [0., 1.]])
    real_run = runner.run
    monkeypatch.setattr(runner, "run", lambda args, paths, effective: real_run(args, paths, effective, ranker=ranking))
    assert cli.main(["watch", "--workspace", str(paths.workspace), "--candidate-policy", "center-recall-v1",
                     "--latent-encoder-cache", "offline-cache", "--machine-result"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 3 and result["candidate_policy"] == "center-recall-v1"
    schema = json.loads((ROOT / "zotwatch/resources/latent-topic-run-result-v3.schema.json").read_text())
    jsonschema.validate(result, schema)
    invalid = {**result, "ranking_v2": True}
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(invalid, schema)


@pytest.mark.parametrize("command,policy,flags", [
    ("profile", "topic-v1", ["--candidate-policy", "center-recall-v1"]),
    ("watch", "legacy-v1", ["--candidate-policy", "center-recall-v1"]),
    ("watch", "topic-v1", ["--latent-recall-model", "model.json"]),
])
def test_cli_rejects_unsupported_scope_before_storage(pipeline, monkeypatch, capsys, command, policy, flags):
    from zotwatch import cli
    import yaml
    paths, _, _, _ = pipeline
    path = paths.workspace / "zotwatch.yaml"
    config = yaml.safe_load(path.read_text()); config["ranking"]["policy"] = policy
    path.write_text(yaml.safe_dump(config))
    assert cli.main([command, "--workspace", str(paths.workspace), "--machine-result", *flags]) == 2
    assert "CONFIG_OPTION_UNSUPPORTED" in capsys.readouterr().out
    assert not (paths.state / "profile.sqlite").exists()
