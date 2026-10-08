"""P5B5 opt-in collection -> frozen center recall -> unchanged topic-v1 boundary."""
from pathlib import Path
import os

from .center_recall import CenterRecallIndex, encode_candidates, MAX_CANDIDATES
from .center_recall_contract import load_center_model, V1_THRESHOLD

COMPATIBILITY_POLICY = "confirmed-topic-candidates-v1"


class LatentRecallUnavailable(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def work_key(candidate):
    return candidate.doi or f"{candidate.source}:{candidate.identifier}"


def encoder_cache():
    return Path(os.getenv("HUGGINGFACE_HUB_CACHE") or os.getenv("HF_HUB_CACHE") or
                str(Path(os.getenv("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"))


class LatentRecallRuntime:
    def __init__(self, model_path, cache_dir):
        try:
            self.model = load_center_model(model_path)
            if self.model.threshold != V1_THRESHOLD or not self.model.centers:
                raise ValueError("Frozen v1 threshold and nonempty formal centers required")
            self.index = CenterRecallIndex(self.model)
        except (OSError, ValueError, TypeError, KeyError):
            raise LatentRecallUnavailable("LATENT_RECALL_ARTIFACT_UNAVAILABLE") from None
        self.cache_dir = cache_dir

    def metadata(self):
        return {"interest_model_revision": self.model.interest_model_revision,
                "embedding_text_fingerprint": self.model.embedding_text_fingerprint,
                "recall_policy": self.model.recall_policy, "threshold": self.model.threshold}

    def recall(self, candidates):
        # First occurrence per existing work identity; no individual-NN admission.
        by_key = {}
        for candidate in candidates:
            by_key.setdefault(work_key(candidate), candidate)
        if len(by_key) > MAX_CANDIDATES:
            raise LatentRecallUnavailable("LATENT_RECALL_CANDIDATE_BUDGET")
        records, missing = [], set()
        for key, candidate in by_key.items():
            if not candidate.abstract or not candidate.abstract.strip():
                missing.add(key)
            else:
                records.append({"id": key, "title": candidate.title, "abstract": candidate.abstract})
        try:
            vectors, encoding = encode_candidates(self.model, self.cache_dir, records)
            decisions = self.index.recall([r["id"] for r in records], vectors,
                embedding_text_fingerprint=encoding["embedding_text_fingerprint"])
        except Exception:
            # Encoder loading, checksum, policy, dimension and fingerprint failures
            # all invalidate this revision for the run; never invoke legacy recall.
            raise LatentRecallUnavailable("LATENT_RECALL_ENCODER_UNAVAILABLE") from None
        decisions = {r["candidate_id"]: {**r, "qualification": "recalled" if r["recalled"] else "below_threshold"}
                     for r in decisions}
        for key in missing:
            decisions[key] = {"candidate_id": key, "recalled": False, "best_match": None,
                "secondary_matches": [], **self.metadata(), "qualification": "insufficient_semantic_text"}
        results = [decisions[key] for key in by_key]
        return [by_key[r["candidate_id"]] for r in results if r["recalled"]], results


def attach_recall(ranked, decisions, confirmed_profile_revision):
    evidence = {r["candidate_id"]: r for r in decisions if r["recalled"]}
    return [{**row, "latent_recall": evidence[row["work_key"]],
             "confirmed_profile_revision": confirmed_profile_revision} for row in ranked]
