"""Offline real-encoder P5B5 pipeline replay; never uses live credentials/publishes."""
import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import cli as engine, fetch_new
from src.models import CandidateWork, ZoteroItem
from src.vectorizer import TextVectorizer
from zotwatch.interests import runner
from zotwatch.interests.contract import PROFILE_PATH
from zotwatch.interests.git import read_local
from zotwatch.interests.integration_results import IntegrationRecommendation, IntegrationRunResult
from zotwatch.interests.ranking import rank
from zotwatch.interests.recall_integration import LatentRecallRuntime, attach_recall, work_key
from zotwatch.paths import RuntimePaths
from zotwatch.runtime import load_effective_runtime


def file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def forbidden(*args, **kwargs):
    raise AssertionError("P5B5 smoke forbids network")


def commit_profile(workspace, profile):
    path = workspace / PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, profile)
    for args in (("init", "-q", "-b", "zotwatch-feedback"),
                 ("config", "user.name", "P5B5 Offline Smoke"),
                 ("config", "user.email", "p5b5@example.invalid"),
                 ("add", PROFILE_PATH), ("commit", "-qm", "Synthetic confirmed smoke profile")):
        subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)


def smoke(model_path, cache, evaluation, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    model_path, cache, evaluation = Path(model_path).resolve(), Path(cache).resolve(), Path(evaluation).resolve()
    history = json.loads((evaluation / "comparison.json").read_text())
    records = [row["candidate"] for row in history["records"]]
    works = [CandidateWork(source=r["id"].split(":", 1)[0], identifier=r["id"].split(":", 1)[1],
        title=r["title"], abstract=r["abstract"], doi=r.get("doi"), url=r["url"],
        published=datetime.fromisoformat(r["date"]).replace(tzinfo=timezone.utc)) for r in records]
    protected = [model_path, evaluation / "comparison.json", evaluation / "candidate_embeddings.npy"]
    protected += sorted((ROOT / "data/latent/frozen/p5b3-v1").rglob("*"))
    protected = [p for p in protected if p.is_file()]
    before = {str(p): file_sha(p) for p in protected}
    profile = json.loads((ROOT / "tests/fixtures/p5b5/confirmed-profile.json").read_text())
    write_json(output / "confirmed-profile.json", profile)

    # Each as-of replay retains original publication dates and the unchanged
    # seven-day admission rule. No fake recent dates or relaxed ranking policy.
    class ReplayDateTime(datetime):
        current = datetime(2026, 10, 1, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    runtime = LatentRecallRuntime(model_path, cache)
    snapshot_path = cache / ("models--" + runtime.model.encoder.model_identifier.replace("/", "--")) / "snapshots" / runtime.model.encoder.model_revision
    class LocalTopicVectorizer(TextVectorizer):
        def load(self):
            if self._model is None:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(str(snapshot_path), device="cpu", local_files_only=True,
                    trust_remote_code=False, model_kwargs={"use_safetensors": True})

    def ingest(self, **kwargs):
        self.storage.initialize()
        for row in json.loads((ROOT / "tests/fixtures/zotero.json").read_text()):
            item = ZoteroItem.from_zotero_api(row)
            self.storage.upsert_item(item, item.key)
        self.storage.set_library_identity_sha256(kwargs["library_identity_sha256"])
        self.storage.set_last_modified_version(10)

    environment = {"ZOTERO_USER_ID": "123456", "ZOTERO_API_KEY": "offline-synthetic-key",
        "ZOTWATCH_WORKSPACE_REPOSITORY_ID": "123", "GITHUB_ACTIONS": "false",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    with tempfile.TemporaryDirectory(prefix="p5b5-smoke-") as temp, \
         patch.dict(os.environ, environment), \
         patch.object(socket.socket, "connect", forbidden), \
         patch.object(requests.sessions.Session, "send", forbidden), \
         patch.object(engine.ZoteroIngestor, "run", ingest), \
         patch.object(engine.build_profile_module, "TextVectorizer", LocalTopicVectorizer), \
         patch.object(engine, "datetime", ReplayDateTime), \
         patch.object(fetch_new, "datetime", ReplayDateTime):
        workspace = Path(temp)
        config = {"schema_version": 2, "zotero": {"library_type": "user"},
            "candidates": {"provider": "public-api-v1", "sources": ["crossref"], "window_days": 7},
            "ranking": {"policy": "topic-v1", "top_n": 200, "max_preprint_ratio": .3},
            "embedding": {"provider": "local", "model": "sentence-transformers/all-MiniLM-L6-v2"},
            "ai": {"services": {}, "features": {"rerank": {"enabled": False}, "summary": {"enabled": False}}},
            "outputs": {"formats": ["json"], "publish": False}}
        (workspace / "zotwatch.yaml").write_text(yaml.safe_dump(config))
        commit_profile(workspace, profile)
        original = read_local(workspace)
        paths, effective = RuntimePaths.resolve(workspace=workspace), load_effective_runtime(workspace)
        copied = paths.state / "latent/recall/center-recall-v1/model.json"
        copied.parent.mkdir(parents=True); shutil.copyfile(model_path, copied)
        args = SimpleNamespace(command="watch", full=True, weekly=False,
            candidate_policy="center-recall-v1", latent_encoder_cache=cache)
        # Seed the real CandidateFetcher transport cache with the preserved public
        # response cohort. Collection still uses its production parsing/outcome code.
        cache_path = paths.state / "cache/candidate_cache.json"
        cache_path.parent.mkdir(parents=True)
        def seed_cache():
            write_json(cache_path, {"fetched_at": ReplayDateTime.current.isoformat(),
                "candidates": [fetch_new.CandidateFetcher._serialize_candidate(c) for c in works
                               if c.published <= ReplayDateTime.current]})
        seed_cache()
        collected = engine.CandidateFetcher(effective.settings, workspace, state_dir=paths.state).fetch_with_outcome()
        assert collected.status == "succeeded" and collected.used_cache and len(collected.candidates) == 58
        recalled, decisions = runtime.recall(collected.candidates)
        by_key = {work_key(c): r for c, r in zip(works, records)}
        assert [r["recalled"] for r in decisions] == [r["centroid"]["recalled"] for r in history["records"]]
        false_positives = [work_key(c) for c, old in zip(works, history["records"])
                          if old["candidate"]["expected"] is False and old["legacy"]["recalled"]]
        assert len(false_positives) == 8
        assert not set(false_positives) & {work_key(c) for c in recalled}
        # Whole-cohort boundary comparison, before unchanged temporal admission.
        baseline = rank(original.profile, recalled, LocalTopicVectorizer())
        integrated = attach_recall(baseline, decisions, original.profile_blob_sha)
        assert [{k: row[k] for k in old} for row, old in zip(integrated, baseline)] == baseline
        for row in integrated:
            IntegrationRecommendation.model_validate(row)
        write_json(output / "cohort-pipeline.json", {"collection_count": 58,
            "recall_decisions": decisions, "ranking_count": len(integrated), "recommendations": integrated,
            "confirmed_profile_revision": original.profile_blob_sha,
            "feedback_commit_sha": original.feedback_commit_sha,
            "scope": "whole-cohort collection/recall/ranking boundary; temporal admission checked in runner replays"})
        categories = ("single-cell", "crispr", "nrt", "salt", "annotation")
        runs = []
        for category in categories:
            chosen = max((r for r in records if r["sampling_category"] == "positive-" + category and r["expected"] is True), key=lambda r: r["date"])
            ReplayDateTime.current = datetime.fromisoformat(chosen["date"]).replace(tzinfo=timezone.utc) + timedelta(days=1)
            seed_cache()
            seen = []
            def observed_rank(profile, candidates, vectorizer):
                seen.extend(work_key(c) for c in candidates)
                return rank(profile, candidates, vectorizer)
            result = runner.run(args, paths, effective, ranker=observed_rank)
            assert result.status == "succeeded", result
            assert any(by_key[key]["sampling_category"] == "positive-" + category for key in seen)
            assert any(by_key[r.work_key]["sampling_category"] == "positive-" + category for r in result.recommendations)
            assert result.evidence.profile_blob_sha == original.profile_blob_sha
            assert result.evidence.latent_recall.interest_model_revision == runtime.model.interest_model_revision
            assert not set(false_positives) & set(seen)
            recorded = paths.state / "runs" / f"topic-{result.run_id}.json"
            assert IntegrationRunResult.model_validate_json(recorded.read_bytes()) == result
            shutil.copyfile(recorded, output / f"run-{category}.json")
            runs.append({"category": category, "as_of": ReplayDateTime.current.isoformat(),
                "run_id": result.run_id, "ranking_input_count": len(seen), "recommendation_count": len(result.recommendations),
                "ranking_input_keys": seen, "evidence_file": f"run-{category}.json"})
            print(category, result.status, len(result.recommendations), flush=True)
        muted = json.loads(json.dumps(profile))
        for topic in muted["interests"]: topic["status"] = "muted"
        commit_profile(workspace, muted)
        with patch.object(engine.CandidateFetcher, "fetch_with_outcome", forbidden):
            paused = runner.run(args, paths, effective)
        assert paused.status == "paused" and paused.recommendations == []
        write_json(output / "all-muted.json", paused.model_dump())
        absent = runner.run(args, paths, effective, snapshot_loader=lambda _: None)
        assert absent.status == "not_ready"
        write_json(output / "no-confirmed-profile.json", absent.model_dump())
    after = {str(p): file_sha(p) for p in protected}
    assert before == after
    report = {"status": "PASS", "scope": "bounded offline public-cohort/real-encoder pipeline integration",
        "candidate_count": 58, "recalled_count": len(recalled), "whole_cohort_ranked_count": len(integrated),
        "removed_historical_individual_nn_false_positives": false_positives,
        "heldout_false_positive_count": sum(old["candidate"]["split"] == "heldout" and work_key(c) in false_positives for c, old in zip(works, history["records"])),
        **runtime.metadata(), "confirmed_profile_revision": original.profile_blob_sha,
        "feedback_commit_sha": original.feedback_commit_sha, "model_file_sha256": file_sha(model_path),
        "encoder": runtime.model.encoder.model_dump(), "runs": runs,
        "all_muted": "paused", "no_confirmed_profile": "not_ready", "ranking_scores_unchanged": True,
        "protected_artifacts_unchanged": before, "live_network_calls": 0, "llm_calls": 0, "rollout": False,
        "limitations": "Public candidate content is real; confirmed profile and Zotero sync are explicit synthetic fixtures. Historical dates replayed; not a live-service or staging acceptance."}
    write_json(output / "smoke.json", report)
    print(json.dumps({k: report[k] for k in ("status", "candidate_count", "recalled_count", "whole_cohort_ranked_count", "all_muted")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "cache", "evaluation", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    smoke(args.model, args.cache, args.evaluation, args.output)
