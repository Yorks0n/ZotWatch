"""Read-only auto_spherical evaluation using the frozen P5B3 embedding cache."""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import sqlite3
import sys
import time

import numpy as np
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotwatch.interests.auto_spherical import auto_spherical
from zotwatch.interests.clustering_benchmark import SOURCE_EMBEDDINGS_SHA256, evaluate_partition
from zotwatch.interests.latent import LatentModelStore


def evaluate(state_dir, output_dir):
    state, output = Path(state_dir).resolve(), Path(output_dir).resolve()
    if output == state or state in output.parents or output in state.parents:
        raise ValueError("Evaluation output must be separate from source state")
    if output.exists():
        raise ValueError("Evaluation output must be a new private directory")
    store = LatentModelStore(state)
    before = (store.root / "current.json").read_bytes()
    model, vectors = store.load()
    if vectors.shape != (1527, 384) or model.embeddings_sha256 != SOURCE_EMBEDDINGS_SHA256:
        raise ValueError("Evaluation requires the frozen 1527-vector P5B3 input")
    with closing(sqlite3.connect((state / "profile.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
        title_by_key = dict(conn.execute("SELECT key,title FROM items"))
    keys = model.embedding_keys
    titles = [title_by_key[key] for key in keys]
    started = time.monotonic()
    with threadpool_limits(limits=1):
        labels, info = auto_spherical(vectors)
        diagnostics, labels = evaluate_partition(vectors, labels, keys, titles)
    elapsed = time.monotonic() - started
    current, current_vectors = store.load()
    if (store.root / "current.json").read_bytes() != before or current != model or not np.array_equal(vectors, current_vectors):
        raise ValueError("Source latent state changed during evaluation")
    payload = {"schema_name": "zotwatch-auto-spherical-evaluation", "schema_version": 1,
        "source_model_revision": model.model_revision, "source_embeddings_sha256": model.embeddings_sha256,
        "source_pointer_sha256": sha256(before).hexdigest(), "input_shape": list(vectors.shape),
        "selector": info, "diagnostics": diagnostics, "elapsed_seconds": elapsed,
        "encoded_items": 0, "llm_calls": 0, "state_mutations": 0,
        "runtime": {"python": sys.version.split()[0],
            **{name: version(name) for name in ("numpy", "scikit-learn")}}}
    output.mkdir(parents=True, exist_ok=False)
    (output / "evaluation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    np.savez_compressed(output / "partition.npz", labels=labels)
    (output / "input_keys.json").write_text(json.dumps(keys) + "\n")
    return payload


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output-dir", required=True, help="New private local output, separate from source")
    args = parser.parse_args(arguments)
    result = evaluate(args.state_dir, args.output_dir)
    print(json.dumps({"selection": result["selector"]["selection"],
        "fit_count": result["selector"]["fit_count"], "elapsed_seconds": result["elapsed_seconds"],
        "summary": result["diagnostics"]["summary"], "encoded_items": 0,
        "llm_calls": 0, "state_mutations": 0}, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
