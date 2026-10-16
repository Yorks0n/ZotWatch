"""Three fixed encoders, same frozen records, eligible-only fixed formation pipeline."""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import numpy as np
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotwatch.interests.clustering_benchmark import SOURCE_EMBEDDINGS_SHA256, evaluate_partition
from zotwatch.interests.embedding_benchmark import MODELS, encode_existing_policy, snapshot_path
from zotwatch.interests.interest_formation import form_interest_model
from zotwatch.interests.latent import LatentModelStore

CORE_REFERENCES = {"C04": "genome annotation", "C06": "single-cell/spatial",
    "C08": "halophyte/general salt tolerance", "C14": "nitrate/NRT2/NRT1", "C16": "CRISPR"}


def file_sha(path):
    checksum = sha256()
    with Path(path).open("rb") as source:
        while data := source.read(1024 * 1024):
            checksum.update(data)
    return checksum.hexdigest()


def run(state_dir, baseline_dir, output_dir, cache_dir):
    state, baseline, output = map(lambda path: Path(path).resolve(), (state_dir, baseline_dir, output_dir))
    if output.exists() or any(output == source or source in output.parents or output in source.parents for source in (state, baseline)):
        raise ValueError("Output must be a new private directory separate from input")
    store = LatentModelStore(state)
    pointer = (store.root / "current.json").read_bytes()
    source_model, old_vectors = store.load()
    if old_vectors.shape != (1527, 384) or source_model.embeddings_sha256 != SOURCE_EMBEDDINGS_SHA256:
        raise ValueError("Benchmark requires the frozen 1527-record evaluation input")
    keys = source_model.embedding_keys
    with closing(sqlite3.connect((state / "profile.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
        lookup = {row[0]: row[1:] for row in conn.execute("SELECT key,title,abstract,doi FROM items")}
    titles, abstracts, dois = map(list, zip(*(lookup[key] for key in keys)))
    texts = ["\n".join(part.strip() for part in (title, abstract or "") if part.strip()) for title, abstract in zip(titles, abstracts)]
    if any(sha256(text.encode()).hexdigest() != source_model.item_text_hashes[key] for key, text in zip(keys, texts)):
        raise ValueError("Metadata no longer matches frozen semantic input")
    input_sha = sha256(json.dumps(list(zip(keys, texts)), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if json.loads((baseline / "input_keys.json").read_text()) != keys:
        raise ValueError("Baseline key alignment mismatch")
    with np.load(baseline / "partitions.npz", allow_pickle=False) as source:
        core_labels = source["hdbscan-10-1-leaf"].copy()
    snapshots = {spec.key: snapshot_path(cache_dir, spec) for spec in MODELS}
    if any(not (path / "model.safetensors").is_file() for path in snapshots.values()):
        raise ValueError("Download reviewed fixed snapshots first; inference is offline only")
    # No network downloads or inference API. Only released safetensors; no remote code.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(1)
    output.mkdir(parents=True, exist_ok=False)
    (output / "input_snapshot.json").write_text(json.dumps({"keys": keys, "titles": titles, "abstracts": abstracts, "dois": dois}, ensure_ascii=False, indent=2) + "\n")
    completed = []
    for spec in MODELS:
        model_dir = output / spec.key
        model_dir.mkdir()
        started = time.monotonic()
        encoder = SentenceTransformer(str(snapshots[spec.key]), device="cpu", local_files_only=True,
            trust_remote_code=False, model_kwargs={"use_safetensors": True})
        if encoder.get_sentence_embedding_dimension() != spec.dimension:
            raise ValueError("Model dimension does not match reviewed specification")
        last_printed = 0
        def progress(done, total):
            nonlocal last_printed
            if time.monotonic() - last_printed >= 30 or done == total:
                print(json.dumps({"encoding": spec.key, "chunks_done": done, "chunks_total": total}), flush=True)
                last_printed = time.monotonic()
        embeddings, encoding = encode_existing_policy(encoder, texts, progress=progress)
        encoded_seconds = time.monotonic() - started
        np.save(model_dir / "embeddings.npy", embeddings, allow_pickle=False)
        del encoder
        with threadpool_limits(limits=1):
            formation = form_interest_model(embeddings, keys, titles, abstracts, dois=dois)
            eligible = formation["eligible_input_rows"]
            qualified = formation["qualification"]
            raw_diagnostics, _ = evaluate_partition(embeddings[eligible], np.array(qualified["raw_labels"], dtype=np.int64),
                [keys[row] for row in eligible], [titles[row] for row in eligible])
        index = {key: i for i, key in enumerate(formation["eligible_keys"])}
        id_by_label = {group["raw_label"]: group["id"] for group in qualified["clusters"]}
        core_checks = []
        for cid, topic in CORE_REFERENCES.items():
            original = np.flatnonzero(core_labels == int(cid[1:]) - 1)
            destinations, raw_destinations, excluded = {}, {}, []
            eligible_core = 0
            for row in original:
                position = index.get(keys[row])
                if position is None:
                    continue
                eligible_core += 1
                member = qualified["members"][position]
                raw_id = id_by_label[member["raw_label"]]
                raw_destinations[raw_id] = raw_destinations.get(raw_id, 0) + 1
                if member["formal_strong"]:
                    destinations[raw_id] = destinations.get(raw_id, 0) + 1
                else:
                    excluded.append({"key": keys[row], "reasons": member["weak_noise_reasons"]})
            core_checks.append({"reference": topic, "hdb_core": cid, "original_count": len(original),
                "eligible_count": eligible_core, "formal_strong_count": sum(destinations.values()),
                "formal_destinations": destinations, "raw_destinations": raw_destinations, "excluded_eligible": excluded})
        manifest = {str(path.relative_to(snapshots[spec.key])): file_sha(path) for path in sorted(snapshots[spec.key].rglob("*")) if path.is_file()}
        result = {"model": spec.__dict__, "encoding": encoding, "input_sha256": input_sha,
            "embedding_file_sha256": file_sha(model_dir / "embeddings.npy"), "snapshot_file_sha256": manifest,
            "encoding_seconds": encoded_seconds, "elapsed_seconds": time.monotonic() - started,
            "fresh_encoded_records": len(texts), "formation": formation,
            "raw_diagnostics": raw_diagnostics, "core_checks": core_checks,
            "old_baseline_vector_comparison": {"maximum_absolute_difference": float(np.max(np.abs(embeddings - old_vectors))),
                "minimum_same_record_cosine": float(np.min(np.sum(embeddings * old_vectors, axis=1)))} if spec.role == "baseline" else None}
        (model_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        np.savez_compressed(model_dir / "eligible_partitions.npz", raw_labels=np.array(qualified["raw_labels"], dtype=np.int64),
            formal_labels=np.array(qualified["formal_labels"], dtype=np.int64), eligible_input_rows=np.array(eligible))
        completed.append(result)
        print(json.dumps({"completed": spec.key, "encoding_seconds": encoded_seconds, "summary": formation["summary"]}), flush=True)
    current, current_vectors = store.load()
    if current != source_model or not np.array_equal(current_vectors, old_vectors) or (store.root / "current.json").read_bytes() != pointer:
        raise ValueError("Original latent state changed during benchmark")
    payload = {"schema_name": "zotwatch-embedding-model-benchmark", "schema_version": 1,
        "date": "2026-10-01", "input_count": len(keys), "input_sha256": input_sha,
        "source_model_revision": source_model.model_revision, "source_embeddings_sha256": source_model.embeddings_sha256,
        "source_pointer_sha256": sha256(pointer).hexdigest(), "results": completed,
        "fresh_encoded_records": sum(result["fresh_encoded_records"] for result in completed), "llm_calls": 0,
        "original_state_mutations": 0, "inference_offline": True,
        "runtime": {"python": sys.version.split()[0], **{name: version(name) for name in ("numpy", "torch", "sentence-transformers", "transformers", "scikit-learn")}, "device": "cpu", "torch_threads": 1, "clustering_blas_threads": 1}}
    (output / "benchmark.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return payload


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("state-dir", "baseline-dir", "output-dir", "cache-dir"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(arguments)
    run(args.state_dir, args.baseline_dir, args.output_dir, args.cache_dir)


if __name__ == "__main__":
    main()
