"""Pre-reviewed public papers, bounded recall calibration and heldout comparison."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import re
from pathlib import Path
import sys
import unicodedata

import numpy as np
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmarks.dual_gate_recall import CenterRecallIndex, RecallParameters, RECALL_POLICY
from zotwatch.interests.frozen_candidate import canonical, digest, encode_candidates, file_digest, load_frozen


def evaluation_title_identity(title):
    """Tiny overlap audit ONLY for evaluation; never changes library work identity."""
    return " ".join(unicodedata.normalize("NFKC", re.sub(r"<[^>]+>", "", title)).casefold().split()).rstrip(" .")


def metrics(records, decisions, split):
    rows = [(r, d) for r, d in zip(records, decisions) if r["split"] == split and r["expected"] is not None]
    tp = sum(r["expected"] and d for r, d in rows)
    fp = sum(not r["expected"] and d for r, d in rows)
    positives = sum(r["expected"] for r, _ in rows)
    negatives = len(rows) - positives
    return {"tp": int(tp), "fp": int(fp), "fn": int(positives-tp), "tn": int(negatives-fp),
        "positive_recall": float(tp / positives) if positives else 0.,
        "false_positive_rate": float(fp / negatives) if negatives else 0.,
        "recalled": int(tp+fp), "scored_count": len(rows)}


def evaluate(capsule, cache, cohort, output, cached_vectors=None):
    output = Path(output)
    if output.exists():
        raise ValueError("A recall experiment cannot overwrite prior evidence")
    snapshot, inputs, history = load_frozen(capsule)
    reviewed = json.loads(Path(cohort).read_text())
    records = reviewed["records"]
    historical_titles = {evaluation_title_identity(title) for title in inputs["titles"]}
    if any(evaluation_title_identity(record["title"]) in historical_titles for record in records):
        raise ValueError("Evaluation cohort contains an already historical paper; remove leakage before encoding")
    cohort_sha = file_digest(cohort)
    output.mkdir(parents=True)
    if cached_vectors is None:
        vectors, encoding = encode_candidates(capsule, cache, records)
    else:
        # Reuse the same cohort's encoded texts after a documented calibration-only
        # amendment; do not re-encode or allow a candidate/order change.
        original = json.loads(Path(cohort).with_name("reviewed_candidates.json").read_text())
        if records != original["records"]:
            raise ValueError("Cached vectors require identical ordered records")
        vectors = np.load(cached_vectors, allow_pickle=False)
        encoding = {"text_policy": snapshot["policy"]["text_policy"], "record_count": len(records),
            "reused_vector_sha256": file_digest(cached_vectors), "source": "same-cohort-initial-calibration"}
    np.save(output / "candidate_embeddings.npy", vectors, allow_pickle=False)
    with threadpool_limits(limits=1):
        index = CenterRecallIndex(snapshot, history)
        legacy_scores = np.clip(vectors @ history.T, -1., 1.)
        centroid_scores = np.clip(vectors @ index.centroids.T, -1., 1.)
        protocol = reviewed["calibration_protocol"]
        trials = []
        for method in ("legacy", "centroid", "dual"):
            presets = protocol[method+"_thresholds"] if method != "dual" else protocol["dual_presets"]
            for preset in presets:
                if method == "dual":
                    parameters = RecallParameters(*preset)
                    evidence = index.recall([r["id"] for r in records], vectors, parameters)
                    decisions = [row["recalled"] for row in evidence]
                else:
                    source = legacy_scores if method == "legacy" else centroid_scores
                    decisions = (source.max(axis=1) >= preset).tolist()
                trials.append({"method": method, "preset": preset, "calibration": metrics(records, decisions, "calibration")})
        chosen = {}
        for method in ("legacy", "centroid", "dual"):
            options = [trial for trial in trials if trial["method"] == method and trial["calibration"]["positive_recall"] >= protocol["minimum_positive_recall"]]
            if not options:
                (output / "calibration_failure.json").write_bytes(canonical({"failed_method": method,
                    "trials": trials, "cohort_sha256": cohort_sha, "heldout_consulted": False}))
                raise ValueError(f"Calibration failed for {method}; don't consult heldout or silently lower target")
            chosen[method] = min(options, key=lambda trial: (trial["calibration"]["fp"], -trial["calibration"]["tp"], trials.index(trial)))
        parameters = RecallParameters(*chosen["dual"]["preset"])
        evidence = index.recall([r["id"] for r in records], vectors, parameters)
        assert evidence == index.recall([r["id"] for r in records], vectors, parameters)
        decisions = {"legacy": (legacy_scores.max(axis=1) >= chosen["legacy"]["preset"]).tolist(),
            "centroid": (centroid_scores.max(axis=1) >= chosen["centroid"]["preset"]).tolist(),
            "dual": [row["recalled"] for row in evidence]}
        comparison = []
        for row, record in enumerate(records):
            nearest = int(legacy_scores[row].argmax()); center = int(centroid_scores[row].argmax())
            comparison.append({"candidate": record, "legacy": {"recalled": decisions["legacy"][row],
                    "nearest_key": inputs["keys"][nearest], "nearest_title": inputs["titles"][nearest],
                    "cosine": float(legacy_scores[row, nearest]),
                    "nearest_eligible": bool((inputs["abstracts"][nearest] or "").strip())},
                "centroid": {"recalled": decisions["centroid"][row],
                    "interest_id": index.centers[center]["interest_id"],
                    "raw_alias": index.centers[center]["raw_cluster_alias"],
                    "cosine": float(centroid_scores[row, center])}, "dual": evidence[row]})
    payload = {"schema_name": "zotwatch-center-recall-probe", "schema_version": 1,
        "interest_model_revision": snapshot["model_revision"], "exact_snapshot_sha256": digest(snapshot),
        "input_digest": snapshot["input_digest"], "cohort_sha256": cohort_sha,
        "candidate_vector_sha256": file_digest(output / "candidate_embeddings.npy"),
        "recall_policy": RECALL_POLICY, "parameters": asdict(parameters), "encoding": encoding,
        "calibration_trials": trials, "selected": chosen,
        "metrics": {method: {split: metrics(records, rows, split) for split in ("calibration", "heldout")} for method, rows in decisions.items()},
        "records": comparison, "deterministic_recall": True, "llm_calls": 0, "rollout": False,
        "scope": reviewed["scope"], "reviewer": reviewed["reviewer"],
        "source_sha256": {name: file_digest(Path(__file__).resolve().parents[1] / name) for name in
            ("scripts/benchmarks/dual_gate_recall.py", "zotwatch/interests/frozen_candidate.py", "scripts/evaluate_center_recall.py")}}
    (output / "comparison.json").write_bytes(canonical(payload))
    print(json.dumps({"selected": chosen, "metrics": payload["metrics"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("capsule", "cache", "cohort", "output"):
        parser.add_argument("--"+name, required=True)
    parser.add_argument("--cached-vectors")
    args = parser.parse_args()
    evaluate(args.capsule, args.cache, args.cohort, args.output, args.cached_vectors)
