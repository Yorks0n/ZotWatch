"""Bounded real-library acceptance and private immutable candidate capsule."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import numpy as np
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zotwatch.interests.frozen_candidate import POLICY, canonical, digest, file_digest, load_frozen
from zotwatch.interests.interest_formation import form_interest_model
from zotwatch.interests.latent import LatentModelStore, make_centers
from zotwatch.interests.latent_contract import LatentParameters


def freeze(benchmark, state, output):
    benchmark, output = Path(benchmark), Path(output)
    if output.exists():
        raise ValueError("Freeze writes a new capsule only; existing versions are immutable")
    result = json.loads((benchmark / "minilm-baseline/result.json").read_text())
    inputs = json.loads((benchmark / "input_snapshot.json").read_text())
    vectors = np.load(benchmark / "minilm-baseline/embeddings.npy", allow_pickle=False)
    frozen_formation = result["formation"]
    def form(x, metadata):
        return form_interest_model(x, metadata["keys"], metadata["titles"], metadata["abstracts"], dois=metadata["dois"])
    with threadpool_limits(limits=1):
        repeated = form(vectors, inputs)
        assert repeated == frozen_formation, "Candidate does not reproduce the reviewed benchmark"
        eligible = repeated["eligible_input_rows"]
        subset = {name: [values[row] for row in eligible] for name, values in inputs.items()}
        only_eligible = form(vectors[eligible], subset)
        assert repeated["qualification"] == only_eligible["qualification"]
        assert repeated["selector"] == only_eligible["selector"]
        altered = vectors.copy()
        ineligible = [row for row in range(len(vectors)) if row not in set(eligible)]
        altered[ineligible] = np.nan
        altered_inputs = deepcopy(inputs)
        for row in ineligible:
            altered_inputs["titles"][row] = "arbitrary title-only evidence must not affect centers"
            altered_inputs["dois"][row] = "10.0000/irrelevant"
        assert form(altered, altered_inputs) == repeated
    q = repeated["qualification"]
    groups = [group for group in q["clusters"] if group["published"]]
    assert repeated["summary"] == {"input_count": 1527, "eligible_count": 1196, "ineligible_count": 331,
        "raw_cluster_count": 30, "formal_center_count": 29, "strong_count": 1053,
        "strong_coverage": 1053/1196, "weak_noise_count": 143, "weak_noise_fraction": 143/1196,
        "total_input_strong_fraction": 1053/1527}
    for group in groups:
        rows = group["probe_strong_rows"]
        assert len({q["members"][row]["work_identity"] for row in rows}) == group["distinct_strong_works"] >= 10
        assert all(q["members"][row]["formal_strong"] for row in rows)
        unit = vectors[eligible][rows]
        unit = unit / np.linalg.norm(unit, axis=1, keepdims=True)
        mean = unit.mean(axis=0); mean /= np.linalg.norm(mean)
        assert np.allclose(mean, group["trimmed_centroid"], atol=1e-14)
        assert all(rep["row"] in rows for rep in group["representatives"])
        assert "GIANT_REVIEW" not in group["flags"]
    store = LatentModelStore(state)
    pointer_before = (store.root / "current.json").read_bytes()
    previous, _ = store.load()
    revision = digest({"policy": POLICY, "input_digest": digest(inputs), "formation_digest": digest(repeated),
        "vectors_sha256": file_digest(benchmark / "minilm-baseline/embeddings.npy")})[:32]
    hashes = {key: digest([title, abstract]) for key, title, abstract in zip(inputs["keys"], inputs["titles"], inputs["abstracts"])}
    # Reuse existing matching/split/merge implementation; no density stability is inferred.
    center_groups = [{"rows": group["probe_strong_rows"], "stability": 0.0} for group in groups]
    centers = make_centers(repeated["eligible_keys"], vectors[eligible], center_groups, revision,
        LatentParameters(), previous, hashes)
    # Subsequent independently created capsules must not reuse one revision for
    # different newly allocated stable IDs. Already frozen capsules are never edited.
    revision = digest({"formation_revision": revision, "stable_id_mapping":
        [center.model_dump(exclude={"model_revision"}) for center in centers]})[:32]
    centers = [center.model_copy(update={"model_revision": revision}) for center in centers]
    exact_centers = []
    for center, group in zip(centers, groups):
        entry = center.model_dump()
        entry["centroid"] = group["trimmed_centroid"]
        entry["representative_items"] = [rep["key"] for rep in group["representatives"]]
        entry.update(raw_cluster_alias=group["id"], distinct_strong_works=group["distinct_strong_works"],
            strong_works=[{"key": q["members"][row]["key"], "work_identity": q["members"][row]["work_identity"],
                "input_row": eligible[row]} for row in group["probe_strong_rows"]])
        exact_centers.append(entry)
    # IDs are persisted once, then the existing matcher must retain all on rebuild.
    rematched = make_centers(repeated["eligible_keys"], vectors[eligible], center_groups, revision,
        LatentParameters(), SimpleNamespace(centers=centers, item_text_hashes=hashes), hashes)
    assert [c.interest_id for c in rematched] == [c.interest_id for c in centers]
    assert (store.root / "current.json").read_bytes() == pointer_before
    snapshot = {"schema_name": "zotwatch-frozen-interest-candidate", "schema_version": 1,
        "model_revision": revision, "policy": POLICY, "input_digest": digest(inputs),
        "benchmark_input_sha256": result["input_sha256"], "formation": repeated,
        "centers": exact_centers, "encoder_artifacts": result["snapshot_file_sha256"],
        "core_checks": result["core_checks"], "runtime": json.loads((benchmark / "benchmark.json").read_text())["runtime"],
        "source_model_revision": previous.model_revision,
        "acceptance": {"status": "PASS", "scope": "bounded-current-real-library-retrieval-structure",
            "exact_partition_and_qualification_reproduced": True, "title_only_invariance": True,
            "all_distinct_strong_support_gte_10": True, "all_centroids_one_pass_trimmed": True,
            "all_representatives_strong": True, "no_giant_cluster": True, "stable_id_roundtrip": True,
            "original_state_unchanged": True},
        "source_sha256": {name: file_digest(Path(__file__).resolve().parents[1] / name) for name in (
            "zotwatch/interests/auto_spherical.py", "zotwatch/interests/clustering_benchmark.py",
            "zotwatch/interests/membership_qualification.py", "zotwatch/interests/interest_formation.py",
            "zotwatch/interests/embedding_benchmark.py", "zotwatch/interests/latent.py")}}
    output.mkdir(parents=True)
    (output / "snapshot.json").write_bytes(canonical(snapshot))
    (output / "inputs.json").write_bytes(canonical(inputs))
    shutil.copyfile(benchmark / "minilm-baseline/embeddings.npy", output / "embeddings.npy")
    manifest = {"policy": POLICY, "model_revision": revision, "exact_snapshot_sha256": digest(snapshot),
        "files": {name: file_digest(output / name) for name in ("snapshot.json", "inputs.json", "embeddings.npy")}}
    (output / "manifest.json").write_bytes(canonical(manifest))
    load_frozen(output)
    print(json.dumps({"revision": revision, "snapshot_sha256": digest(snapshot), "acceptance": snapshot["acceptance"],
        "summary": repeated["summary"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("benchmark", "state", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    freeze(args.benchmark, args.state, args.output)
