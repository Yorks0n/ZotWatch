"""Historical benchmark ONLY: centroid gate AND distinct-work evidence.

Not packaged as runtime. center-recall-v1 has no corroboration gate or members.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
import math

import numpy as np

from zotwatch.interests.frozen_candidate import POLICY, encode_candidates, load_frozen

RECALL_POLICY = "center-supported-recall-experimental-v1"
MAX_CANDIDATES = 2000


@dataclass(frozen=True)
class RecallParameters:
    # Deliberately no threshold defaults: these are NOT membership gates.
    center_threshold: float
    member_threshold: float
    minimum_distinct_support: int

    def __post_init__(self):
        for value in (self.center_threshold, self.member_threshold):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Recall thresholds must be finite cosines in [0,1]")
        if isinstance(self.minimum_distinct_support, bool) or not isinstance(self.minimum_distinct_support, int) or not 2 <= self.minimum_distinct_support <= 20:
            raise ValueError("Recall requires 2..20 distinct supporting works")


def _vectors(value, dimension=None):
    x = np.asarray(value, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] < 1 or (dimension is not None and x.shape[1] != dimension):
        raise ValueError("Recall vector dimension mismatch")
    if not np.isfinite(x).all() or not np.allclose(np.linalg.norm(x, axis=1), 1., atol=1e-6):
        raise ValueError("Recall vectors must be finite and normalized")
    return x


class CenterRecallIndex:
    def __init__(self, snapshot, historical_vectors):
        if snapshot["policy"] != POLICY:
            raise ValueError("Recall accepts only the frozen P5B3 candidate policy")
        self.revision = snapshot["model_revision"]
        self.vectors = _vectors(historical_vectors, POLICY["dimension"]).copy()
        if len(self.vectors) > 20000 or len(snapshot["centers"]) > 60:
            raise ValueError("Recall historical budget exceeded")
        self.centers = sorted(deepcopy(snapshot["centers"]), key=lambda center: center["interest_id"])
        if len({center["interest_id"] for center in self.centers}) != len(self.centers):
            raise ValueError("Duplicate center ID")
        formation = snapshot["formation"]
        eligible = set(formation["eligible_input_rows"])
        evidence = {member["key"]: member for member in formation["qualification"]["members"] if member["formal_strong"]}
        groups = {group["id"]: group for group in formation["qualification"]["clusters"] if group["published"]}
        keys = formation["eligible_keys"]
        key_row = {key: row for row, key in zip(formation["eligible_input_rows"], keys)}
        for center in self.centers:
            group = groups.get(center["raw_cluster_alias"])
            if group is None:
                raise ValueError("Recall requires a published raw cluster")
            if center["model_revision"] != self.revision:
                raise ValueError("Center revision mismatch")
            identities = set()
            works = center["strong_works"]
            if len({work["key"] for work in works}) != len(works) or sorted(work["key"] for work in works) != center["member_keys"]:
                raise ValueError("Invalid strong work membership")
            for work in works:
                row = work["input_row"]
                if row not in eligible or row >= len(self.vectors) or key_row.get(work["key"]) != row:
                    raise ValueError("Ineligible or misaligned recall support")
                member = evidence.get(work["key"])
                if member is None or member["work_identity"] != work["work_identity"] or member["formal_label"] != group["raw_label"]:
                    raise ValueError("Recall support must be a qualified strong work")
                identities.add(work["work_identity"])
            if len(identities) != center["distinct_strong_works"] or len(identities) < 10:
                raise ValueError("Unpublished formal center")
            centroid = _vectors([center["centroid"]], self.vectors.shape[1])[0]
            mean = self.vectors[[work["input_row"] for work in works]].mean(axis=0)
            mean /= np.linalg.norm(mean)
            if not np.allclose(mean, centroid, atol=1e-12):
                raise ValueError("Centroid and strong support mismatch")
        self.centroids = np.array([center["centroid"] for center in self.centers], dtype=np.float64).reshape(-1, self.vectors.shape[1])

    def recall(self, candidate_ids, candidate_vectors, parameters):
        """One candidate may match multiple centers; score is best center cosine.

        Duplicate records of one historical work count once (maximum similarity).
        Corroboration sees only strong works in the SAME matched formal center.
        Diagnostics also retain failed possible centers for bounded evaluation.
        """
        if not isinstance(parameters, RecallParameters):
            raise ValueError("Explicit experimental recall parameters required")
        x = _vectors(candidate_vectors, self.vectors.shape[1])
        if len(x) != len(candidate_ids) or len(x) > MAX_CANDIDATES or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("Invalid recall candidate alignment/budget/identities")
        if any(not isinstance(key, str) or not key for key in candidate_ids):
            raise ValueError("Candidate IDs must be nonempty text")
        scores = np.clip(x @ self.centroids.T, -1., 1.)
        results = []
        for candidate_id, vector, score_row in zip(candidate_ids, x, scores):
            possible, accepted = [], []
            for center, center_cosine in zip(self.centers, score_row):
                if center_cosine < parameters.center_threshold:
                    continue
                works = center["strong_works"]
                cosines = np.clip(self.vectors[[work["input_row"] for work in works]] @ vector, -1., 1.)
                distinct = {}
                for work, cosine in zip(works, cosines):
                    entry = {"key": work["key"], "work_identity": work["work_identity"], "cosine": float(cosine)}
                    prior = distinct.get(work["work_identity"])
                    if prior is None or (-entry["cosine"], entry["key"]) < (-prior["cosine"], prior["key"]):
                        distinct[work["work_identity"]] = entry
                support = sorted((entry for entry in distinct.values() if entry["cosine"] >= parameters.member_threshold),
                    key=lambda entry: (-entry["cosine"], entry["key"]))
                qualified = len(support) >= parameters.minimum_distinct_support
                match = {"interest_id": center["interest_id"], "center_cosine": float(center_cosine),
                    "support_count": len(support), "top_supporting_works": support[:max(5, parameters.minimum_distinct_support)],
                    "interest_model_revision": self.revision, "qualified": qualified}
                possible.append(match)
                if qualified:
                    accepted.append(match)
            order = lambda match: (-match["center_cosine"], match["interest_id"])
            possible.sort(key=order); accepted.sort(key=order)
            results.append({"candidate_id": candidate_id, "recalled": bool(accepted),
                "recall_score": accepted[0]["center_cosine"] if accepted else None,
                "best_match": accepted[0] if accepted else None, "secondary_matches": accepted[1:4],
                "matched_center_count": len(accepted), "possible_centers": possible,
                "interest_model_revision": self.revision, "recall_policy": RECALL_POLICY,
                "parameters": asdict(parameters)})
        return results


def recall_candidates(capsule, cache_dir, candidates, parameters):
    """Small callable local slice; intentionally not connected to dispatch/runtime."""
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError("Recall candidate budget exceeded")
    if not isinstance(parameters, RecallParameters):
        raise ValueError("Explicit experimental recall parameters required")
    ids = [record["id"] for record in candidates]
    if any(not isinstance(key, str) or not key for key in ids) or len(set(ids)) != len(ids):
        raise ValueError("Recall requires unique candidate IDs")
    snapshot, _, history = load_frozen(capsule)
    vectors, metadata = encode_candidates(capsule, cache_dir, candidates)
    results = CenterRecallIndex(snapshot, history).recall(ids, vectors, parameters)
    return {"interest_model_revision": snapshot["model_revision"], "encoding": metadata, "results": results}
