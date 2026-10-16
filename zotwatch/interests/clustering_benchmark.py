"""Read-only strategy benchmark. Never builds embeddings or publishes latent state."""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sqlite3
import sys
import time

import numpy as np

from .latent import LatentModelStore, cluster_vectors
from .latent_contract import LatentParameters

SEED = 20260930
K_VALUES = (20, 25, 30, 35)
MIN_SUPPORT = 10
MIN_COSINE = 0.50
MIN_MARGIN = 0.02
SOURCE_EMBEDDINGS_SHA256 = "83a9b59778a2ae2aff7052c68f37ce800fda79cc6d39d0bd9e79f5b0f8bb0c3d"


def _normalize(matrix):
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if not np.isfinite(matrix).all() or np.any(norms <= 1e-12):
        raise ValueError("Invalid clustering vectors")
    return matrix / norms


def spherical_kmeans(vectors, k, *, seed=SEED, restarts=10, max_iter=300):
    """True spherical updates: cosine assignment and normalized mean centroids.

    K-means++ samples proportional to 1-cosine. Select the restart with greatest
    summed assigned cosine, never using semantic judgments or the diagnostic gate.
    """
    x = _normalize(np.asarray(vectors, dtype=np.float64))
    if not 1 <= k <= len(x) or len(np.unique(x, axis=0)) < k:
        raise ValueError("K exceeds the number of distinct vector directions")
    if restarts < 1 or max_iter < 1:
        raise ValueError("Invalid spherical K-means run budget")
    best = None
    for restart in range(restarts):
        rng = np.random.default_rng(seed + restart)
        chosen = [int(rng.integers(len(x)))]
        distance = np.clip(1 - x @ x[chosen[0]], 0, 2)
        for _ in range(1, k):
            distance[chosen] = 0
            if distance.sum() <= 1e-12:
                raise ValueError("Insufficient distinct directions for initialization")
            chosen.append(int(rng.choice(len(x), p=distance / distance.sum())))
            distance = np.minimum(distance, np.clip(1 - x @ x[chosen[-1]], 0, 2))
        centroids = x[chosen].copy()
        previous = None
        converged = False
        for iteration in range(max_iter):
            scores = x @ centroids.T
            labels = np.argmax(scores, axis=1)
            counts = np.bincount(labels, minlength=k)
            # A degenerate empty cluster takes a least-fitting member from a
            # non-singleton donor. This is deterministic and preserves exact K.
            for empty in np.flatnonzero(counts == 0):
                ordered = np.argsort(scores[np.arange(len(x)), labels], kind="stable")
                row = next(int(i) for i in ordered if counts[labels[i]] > 1)
                counts[labels[row]] -= 1
                labels[row] = empty
                counts[empty] += 1
            sums = np.zeros_like(centroids)
            np.add.at(sums, labels, x)
            centroids = _normalize(sums)
            if previous is not None and np.array_equal(labels, previous):
                converged = True
                break
            previous = labels.copy()
        objective = float(np.sum(x * centroids[labels]))
        result = (objective, labels.copy(), {"seed": seed, "restarts": restarts,
            "selected_restart": restart, "selected_seed": seed + restart,
            "iterations": iteration + 1, "converged": converged, "objective": objective})
        if best is None or objective > best[0]:
            best = result
    return best[1], best[2]


def canonical_labels(labels, keys):
    """Number clusters by their first stable item key, independent of backend labels."""
    labels = np.asarray(labels, dtype=np.int64)
    order = sorted(set(labels) - {-1}, key=lambda label: min(keys[i] for i in np.flatnonzero(labels == label)))
    result = np.full(len(labels), -1, dtype=np.int64)
    for number, label in enumerate(order):
        result[labels == label] = number
    return result


def evaluate_partition(vectors, labels, keys, titles):
    x = _normalize(np.asarray(vectors, dtype=np.float64))
    labels = canonical_labels(labels, keys)
    if labels.shape != (len(x),) or len(keys) != len(x) or len(titles) != len(x):
        raise ValueError("Partition input alignment mismatch")
    k = len(set(labels) - {-1})
    if not k:
        raise ValueError("Benchmark partition has no centers")
    rows = [np.flatnonzero(labels == label) for label in range(k)]
    centers = _normalize(np.array([x[members].mean(axis=0) for members in rows]))
    similarities = np.clip(x @ centers.T, -1, 1)
    center_cosine = np.clip(centers @ centers.T, -1, 1)
    np.fill_diagonal(center_cosine, -np.inf)
    assigned = labels >= 0
    assigned_scores = np.full(len(x), np.nan)
    margins = np.full(len(x), np.nan)
    assigned_scores[assigned] = similarities[np.flatnonzero(assigned), labels[assigned]]
    alternatives = similarities.copy()
    alternatives[np.flatnonzero(assigned), labels[assigned]] = -np.inf
    margins[assigned] = assigned_scores[assigned] - alternatives[assigned].max(axis=1) if k > 1 else 2.0
    sizes = np.array([len(members) for members in rows])
    accepted = assigned.copy()
    accepted[assigned] &= (sizes[labels[assigned]] >= MIN_SUPPORT) & (assigned_scores[assigned] >= MIN_COSINE) & (margins[assigned] >= MIN_MARGIN)
    clusters = []
    for label, members in enumerate(rows):
        values = assigned_scores[members]
        weak = values < MIN_COSINE
        ambiguous = margins[members] < MIN_MARGIN
        centroid_disagreement = np.argmax(similarities[members], axis=1) != label
        nearest = int(np.argmax(center_cosine[label])) if k > 1 else None
        nearest_score = float(center_cosine[label, nearest]) if nearest is not None else None
        flags = []
        if len(members) < MIN_SUPPORT:
            flags.append("SMALL_SUPPORT")
        if len(members) >= max(0.15 * len(x), 3 * len(x) / k):
            flags.append("GIANT_REVIEW")
        if float(np.mean(values)) < 0.60:
            flags.append("LOW_COHESION_REVIEW")
        if float(np.mean(weak | ambiguous)) > 0.35:
            flags.append("WEAK_OR_AMBIGUOUS_REVIEW")
        if nearest_score is not None and nearest_score >= 0.85:
            flags.append("CENTER_OVERLAP_REVIEW")
        def paper(row):
            return {"row": int(row), "key": keys[row], "title": titles[row],
                "cosine": float(assigned_scores[row]), "margin": float(margins[row])}
        strongest = sorted(members, key=lambda row: (-assigned_scores[row], keys[row]))[:5]
        weakest = [row for row in sorted(members, key=lambda row: (assigned_scores[row], keys[row])) if row not in strongest][:3]
        # Fixed, spread-out sample avoids inspecting only the dense centroid core.
        middle = [int(row) for row in members if row not in strongest and row not in weakest]
        sample = [middle[i] for i in np.linspace(0, len(middle) - 1, min(3, len(middle)), dtype=int)] if middle else []
        clusters.append({"id": f"C{label+1:02}", "size": len(members),
            "mean_cohesion": float(values.mean()), "median_cohesion": float(np.median(values)),
            "p10_cosine": float(np.quantile(values, 0.1)), "weak_count": int(weak.sum()),
            "ambiguous_count": int(ambiguous.sum()), "supported_count": int(accepted[members].sum()),
            "nearest_center": f"C{nearest+1:02}" if nearest is not None else None,
            "nearest_center_similarity": nearest_score,
            "non_nearest_assignment_count": int(centroid_disagreement.sum()),
            "flags": flags, "representatives": [paper(row) for row in strongest],
            "fringe": [paper(row) for row in weakest], "spread_sample": [paper(row) for row in sample]})
    scores = assigned_scores[assigned]
    summary = {"cluster_count": k, "assigned_items": int(assigned.sum()),
        "coverage": float(assigned.mean()), "outlier_percent": float((~assigned).mean()),
        "supported_items": int(accepted.sum()), "supported_coverage": float(accepted.mean()),
        "diagnostic_outlier_or_weak_percent": float((~accepted).mean()),
        "minimum_cluster_size": int(sizes.min()), "maximum_cluster_size": int(sizes.max()),
        "sizes_sorted": sorted(sizes.tolist()),
        "size_quantiles": [float(value) for value in np.quantile(sizes, [0, .25, .5, .75, 1])],
        "size_bins": {"1-2": int((sizes <= 2).sum()), "3-9": int(((sizes >= 3) & (sizes <= 9)).sum()),
            "10-49": int(((sizes >= 10) & (sizes <= 49)).sum()), "50-99": int(((sizes >= 50) & (sizes <= 99)).sum()),
            "100-199": int(((sizes >= 100) & (sizes <= 199)).sum()), "200+": int((sizes >= 200).sum())},
        "within_cosine_mean": float(scores.mean()), "within_cosine_median": float(np.median(scores)),
        "cluster_mean_cosine_macro": float(np.mean([c["mean_cohesion"] for c in clusters])),
        "cluster_median_cosine_macro": float(np.median([c["median_cohesion"] for c in clusters])),
        "nearest_center_mean": float(np.mean([c["nearest_center_similarity"] for c in clusters])) if k > 1 else None,
        "nearest_center_median": float(np.median([c["nearest_center_similarity"] for c in clusters])) if k > 1 else None,
        "nearest_center_max": float(np.max(center_cosine)) if k > 1 else None,
        "non_nearest_assignment_count": sum(c["non_nearest_assignment_count"] for c in clusters),
        "small_cluster_items": int(sizes[sizes < MIN_SUPPORT].sum()),
        "flagged_cluster_count": sum(bool(c["flags"]) for c in clusters)}
    return {"summary": summary, "clusters": clusters}, labels


def core_transitions(core_labels, other_labels):
    result = []
    for core in sorted(set(core_labels) - {-1}):
        members = np.flatnonzero(core_labels == core)
        labels, counts = np.unique(other_labels[members], return_counts=True)
        order = np.argsort(-counts, kind="stable")
        result.append({"core": f"C{core+1:02}", "size": len(members),
            "dominant_center": f"C{labels[order[0]]+1:02}" if labels[order[0]] >= 0 else "noise",
            "dominant_share": float(counts[order[0]] / len(members)),
            "destinations": [{"center": f"C{labels[i]+1:02}" if labels[i] >= 0 else "noise", "items": int(counts[i])} for i in order]})
    return result


def run_benchmark(state_dir, output_dir):
    from sklearn.cluster import AgglomerativeClustering
    from threadpoolctl import threadpool_limits
    state, output = Path(state_dir).resolve(), Path(output_dir).resolve()
    if output == state or state in output.parents:
        raise ValueError("Benchmark output must be separate from the source state")
    store = LatentModelStore(state)
    pointer_before = (store.root / "current.json").read_bytes()
    model, vectors = store.load()
    if len(vectors) != 1527 or model.embeddings_sha256 != SOURCE_EMBEDDINGS_SHA256:
        raise ValueError("This benchmark requires the frozen 1527-vector evaluation input")
    # Read SQLite only to resolve paper titles. No vectorizer, model or LLM code.
    database = state / "profile.sqlite"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        titles_by_key = dict(conn.execute("SELECT key,title FROM items"))
    keys = model.embedding_keys
    if not set(keys) <= titles_by_key.keys():
        raise ValueError("Representative title lookup is incomplete")
    titles = [titles_by_key[key] for key in keys]
    output.mkdir(parents=True, exist_ok=False)
    results, partitions = [], {}
    with threadpool_limits(limits=1):
        runs = [("hdbscan-10-1-leaf", "HDBSCAN 10/1/leaf", None)] + [
            (f"spherical-kmeans-{k}", f"Spherical K-means K={k}", k) for k in K_VALUES] + [
            (f"cosine-average-{k}", f"Cosine agglomerative / average K={k}", k) for k in K_VALUES]
        for name, title, k in runs:
            started = time.monotonic()
            if name.startswith("hdbscan"):
                parameters = LatentParameters(min_cluster_size=10, min_samples=1, selection="leaf")
                groups = cluster_vectors(keys, vectors, parameters)
                labels = np.full(len(vectors), -1, dtype=np.int64)
                for number, group in enumerate(groups):
                    labels[group["rows"]] = number
                config = parameters.model_dump()
            elif name.startswith("spherical"):
                labels, config = spherical_kmeans(vectors, k)
            else:
                labels = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit_predict(vectors)
                config = {"metric": "cosine", "linkage": "average", "k": k}
            result, labels = evaluate_partition(vectors, labels, keys, titles)
            result.update(name=name, title=title, config=config, elapsed_seconds=time.monotonic() - started)
            results.append(result)
            partitions[name] = labels
            print(json.dumps({"completed": name, **result["summary"]}), flush=True)
    for result in results:
        result["core_transitions"] = core_transitions(partitions[runs[0][0]], partitions[result["name"]])
    if (store.root / "current.json").read_bytes() != pointer_before:
        raise ValueError("Source latent pointer changed during benchmark")
    current, check = store.load()
    if current != model or not np.array_equal(check, vectors):
        raise ValueError("Source latent state changed during benchmark")
    payload = {"schema_name": "zotwatch-clustering-strategy-benchmark", "schema_version": 1,
        "date": "2026-09-30", "source_model_revision": model.model_revision,
        "source_embeddings_sha256": model.embeddings_sha256,
        "source_pointer_sha256": sha256(pointer_before).hexdigest(), "input_shape": list(vectors.shape),
        "embedding_fingerprint": model.embedding_fingerprint, "encoded_items": 0, "llm_calls": 0,
        "state_mutations": 0, "runtime": {"python": sys.version.split()[0], "platform": platform.platform(),
            **{name: version(name) for name in ("numpy", "scipy", "scikit-learn", "hdbscan")}},
        "diagnostic_gate": {"minimum_support": MIN_SUPPORT, "minimum_cosine": MIN_COSINE, "minimum_margin": MIN_MARGIN},
        "results": results}
    (output / "benchmark.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    np.savez_compressed(output / "partitions.npz", **partitions)
    (output / "input_keys.json").write_text(json.dumps(keys) + "\n")
    return payload


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(arguments)
    run_benchmark(args.state_dir, args.output_dir)


if __name__ == "__main__":
    main()
