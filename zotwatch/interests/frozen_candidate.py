"""P5B3 v1 candidate contract, separate from the accepted runtime publisher.

Only the reviewed baseline can encode P5B4 inputs. No model/K/threshold search.
The private capsule is immutable and carries the exact formation and stable IDs.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from .embedding_benchmark import MODELS, TEXT_POLICY, encode_existing_policy, snapshot_path
from .interest_formation import ELIGIBILITY_POLICY_VERSION

BASELINE = MODELS[0]
POLICY = {
    "candidate_version": "p5b3-v1-candidate",
    "eligibility": ELIGIBILITY_POLICY_VERSION,
    "title_only_status": "ineligible_for_interest_model",
    "model_identifier": BASELINE.identifier,
    "model_revision": BASELINE.revision,
    "dimension": BASELINE.dimension,
    "text_policy": TEXT_POLICY,
    "semantic_input": 'title + "\\n" + abstract',
    "formation": "auto-spherical-size-v1",
    "seed": 20260930, "restarts": 10, "max_iter": 300,
    "qualification": "membership-qualification-experimental-v1",
    "minimum_cosine": 0.50, "minimum_margin": 0.02,
    "minimum_distinct_strong_works": 10,
    "formal_centroid": "one-pass-normalized-mean-of-strong-member-unit-vectors",
    "trim_passes": 1, "reassignment": False,
}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return sha256(canonical(value)).hexdigest()


def file_digest(path):
    result = sha256()
    with Path(path).open("rb") as source:
        while data := source.read(1024 * 1024):
            result.update(data)
    return result.hexdigest()


def semantic_text(title, abstract):
    if not isinstance(title, str) or not isinstance(abstract, str) or not abstract.strip():
        raise ValueError("ineligible_for_interest_model: insufficient_semantic_text")
    return title.strip() + "\n" + abstract.strip()


def load_frozen(directory):
    """Reject corrupted or policy-incompatible capsules before recall."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["policy"] != POLICY:
        raise ValueError("Frozen interest policy mismatch")
    for name, checksum in manifest["files"].items():
        if name not in {"snapshot.json", "embeddings.npy", "inputs.json"} or file_digest(directory / name) != checksum:
            raise ValueError("Frozen capsule checksum mismatch")
    if set(manifest["files"]) != {"snapshot.json", "embeddings.npy", "inputs.json"}:
        raise ValueError("Incomplete frozen capsule")
    snapshot = json.loads((directory / "snapshot.json").read_text())
    inputs = json.loads((directory / "inputs.json").read_text())
    vectors = np.load(directory / "embeddings.npy", allow_pickle=False)
    if snapshot["policy"] != POLICY or digest(snapshot) != manifest["exact_snapshot_sha256"]:
        raise ValueError("Frozen snapshot mismatch")
    if snapshot["model_revision"] != manifest["model_revision"] or digest(inputs) != snapshot["input_digest"]:
        raise ValueError("Frozen input/revision mismatch")
    if vectors.shape != (len(inputs["keys"]), BASELINE.dimension) or not np.isfinite(vectors).all():
        raise ValueError("Invalid frozen embedding shape")
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1., atol=1e-6):
        raise ValueError("Frozen embeddings must be normalized")
    return snapshot, inputs, vectors


def encode_candidates(directory, cache_dir, records):
    """Offline fixed-baseline encoder; abstract required, never a title fallback."""
    snapshot, _, _ = load_frozen(directory)
    texts = [semantic_text(record["title"], record.get("abstract")) for record in records]
    path = snapshot_path(cache_dir, BASELINE)
    for name, checksum in snapshot["encoder_artifacts"].items():
        if file_digest(path / name) != checksum:
            raise ValueError("Fixed encoder artifact mismatch")
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(1)
    model = SentenceTransformer(str(path), device="cpu", local_files_only=True,
        trust_remote_code=False, model_kwargs={"use_safetensors": True})
    vectors, metadata = encode_existing_policy(model, texts)
    if vectors.shape != (len(records), BASELINE.dimension) or metadata["text_policy"] != POLICY["text_policy"]:
        raise ValueError("Candidate encoder policy mismatch")
    return vectors, metadata
