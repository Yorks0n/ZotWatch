"""Non-independent mixed-center stress probe; never part of new-paper metrics."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zotwatch.interests.frozen_candidate import load_frozen, digest


def diagnose(capsule):
    snapshot, inputs, vectors = load_frozen(capsule)
    center = next(c for c in snapshot["centers"] if c["raw_cluster_alias"] == "C27")
    queries = []
    # Explicit fixed review sample; this does not filter or blacklist any center.
    for key in ("5YGMI3WV", "EVBR568G", "YJ9H3TCA"):
        query = next(work for work in center["strong_works"] if work["key"] == key)
        row = query["input_row"]
        others = [work for work in center["strong_works"] if work["work_identity"] != query["work_identity"]]
        scores = vectors[[work["input_row"] for work in others]] @ vectors[row]
        distinct = {}
        for work, score in zip(others, scores):
            if score >= .55:
                entry = {"key": work["key"], "title": inputs["titles"][work["input_row"]], "cosine": float(score)}
                prior = distinct.get(work["work_identity"])
                if prior is None or entry["cosine"] > prior["cosine"]:
                    distinct[work["work_identity"]] = entry
        queries.append({"query_key": key, "title": inputs["titles"][row],
            "frozen_centroid_cosine": float(vectors[row] @ center["centroid"]),
            "support_count_excluding_own_work": len(distinct),
            "supports": sorted(distinct.values(), key=lambda entry: -entry["cosine"])[:5]})
    return {"scope": "non-independent existing-library mixed-center diagnostic; query participates in frozen centroid, own distinct work excluded from corroboration only; NOT new-paper validation or part of TP/FP tables",
        "center_id": center["interest_id"], "interest_model_revision": snapshot["model_revision"],
        "exact_snapshot_sha256": digest(snapshot), "center_alias": "C27", "T_center": .55, "T_member": .55, "M": 2,
        "queries": queries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capsule", required=True)
    args = parser.parse_args()
    print(json.dumps(diagnose(args.capsule), ensure_ascii=False, indent=2))
