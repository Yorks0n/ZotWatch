"""Reproduce final P5B4 decisions/evidence from cached vectors, without encoding."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmarks.dual_gate_recall import CenterRecallIndex, RecallParameters
from zotwatch.interests.frozen_candidate import digest, file_digest, load_frozen
from evaluate_center_recall import metrics


def verify(capsule, cohort, evaluation):
    snapshot, inputs, history = load_frozen(capsule)
    evaluation = Path(evaluation)
    result = json.loads((evaluation / "comparison.json").read_text())
    records = json.loads(Path(cohort).read_text())["records"]
    assert file_digest(cohort) == result["cohort_sha256"]
    assert file_digest(evaluation / "candidate_embeddings.npy") == result["candidate_vector_sha256"]
    assert result["interest_model_revision"] == snapshot["model_revision"]
    assert result["exact_snapshot_sha256"] == digest(snapshot)
    assert [row["candidate"] for row in result["records"]] == records
    vectors = np.load(evaluation / "candidate_embeddings.npy", allow_pickle=False)
    with threadpool_limits(limits=1):
        index = CenterRecallIndex(snapshot, history)
        parameters = RecallParameters(**result["parameters"])
        evidence = index.recall([r["id"] for r in records], vectors, parameters)
        assert evidence == [row["dual"] for row in result["records"]]
        legacy = np.clip(vectors @ history.T,-1.,1.)
        centroid = np.clip(vectors @ index.centroids.T,-1.,1.)
        decisions = {"legacy": (legacy.max(axis=1) >= result["selected"]["legacy"]["preset"]).tolist(),
            "centroid": (centroid.max(axis=1) >= result["selected"]["centroid"]["preset"]).tolist(),
            "dual": [r["recalled"] for r in evidence]}
        for method, values in decisions.items():
            assert values == [row[method]["recalled"] for row in result["records"]]
            for split in ("calibration", "heldout"):
                assert metrics(records,values,split) == result["metrics"][method][split]
    print(f"PASS: {len(records)} candidates; exact recall evidence and metrics / {snapshot['model_revision']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("capsule", "cohort", "evaluation"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args()
    verify(args.capsule,args.cohort,args.evaluation)
