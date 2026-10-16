"""center-recall-v1: formal centroid cosine only, no member vectors or gates."""
from hashlib import sha256
from pathlib import Path

import numpy as np

from .center_recall_contract import CenterRecallModel, load_center_model

MAX_CANDIDATES = 2000


def _candidate_ids(ids):
    if len(ids) > MAX_CANDIDATES or any(not isinstance(key, str) or not key for key in ids) or len(set(ids)) != len(ids):
        raise ValueError("Invalid recall candidate IDs or budget")


class CenterRecallIndex:
    def __init__(self, model):
        if not isinstance(model, CenterRecallModel):
            raise ValueError("Validated centroid-only model required")
        # Snapshot metadata copied once. There is no member/representative payload.
        self.model = model.model_copy(deep=True)
        centers = sorted(self.model.centers, key=lambda center: center.interest_id)
        self.ids = tuple(center.interest_id for center in centers)
        self.centroids = np.array([center.centroid for center in centers], dtype=np.float64).reshape(-1, self.model.encoder.dimension)
        self.centroids.flags.writeable = False

    def recall(self, candidate_ids, candidate_vectors, *, embedding_text_fingerprint):
        _candidate_ids(candidate_ids)
        if embedding_text_fingerprint != self.model.embedding_text_fingerprint:
            raise ValueError("Candidate embedding/text-policy fingerprint mismatch")
        x = np.asarray(candidate_vectors, dtype=np.float64)
        if x.shape != (len(candidate_ids), self.model.encoder.dimension):
            raise ValueError("Recall candidate vector alignment/dimension mismatch")
        if not np.isfinite(x).all() or not np.allclose(np.linalg.norm(x, axis=1), 1., atol=1e-6, rtol=0):
            raise ValueError("Recall candidates must be finite normalized vectors")
        scores = np.clip(x @ self.centroids.T, -1., 1.)
        results = []
        for candidate_id, row in zip(candidate_ids, scores):
            matches = [{"interest_id": interest_id, "center_cosine": float(cosine)}
                for interest_id, cosine in zip(self.ids, row) if cosine >= self.model.threshold]
            matches.sort(key=lambda match: (-match["center_cosine"], match["interest_id"]))
            results.append({"candidate_id": candidate_id, "recalled": bool(matches),
                "best_match": matches[0] if matches else None, "secondary_matches": matches[1:4],
                "interest_model_revision": self.model.interest_model_revision,
                "embedding_text_fingerprint": self.model.embedding_text_fingerprint,
                "recall_policy": self.model.recall_policy, "threshold": self.model.threshold})
        return results


def _semantic_text(record):
    title, abstract = record.get("title"), record.get("abstract")
    if not isinstance(title, str) or not isinstance(abstract, str) or not abstract.strip():
        raise ValueError("insufficient_semantic_text: candidate abstract required")
    return title.strip() + "\n" + abstract.strip()


def encode_candidates(model, cache_dir, candidates):
    """Same frozen MiniLM encoder, without reading historical vectors/metadata."""
    _candidate_ids([record["id"] for record in candidates])
    texts = [_semantic_text(record) for record in candidates]
    if not texts:
        return np.empty((0, model.encoder.dimension)), {"text_policy": model.encoder.text_policy,
            "embedding_text_fingerprint": model.embedding_text_fingerprint, "record_count": 0}
    identity = model.encoder
    path = Path(cache_dir) / ("models--" + identity.model_identifier.replace("/", "--")) / "snapshots" / identity.model_revision
    for name, checksum in identity.artifact_sha256.items():
        result = sha256()
        with (path / name).open("rb") as source:
            while data := source.read(1024 * 1024):
                result.update(data)
        if result.hexdigest() != checksum:
            raise ValueError("Fixed encoder artifact mismatch")
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from sentence_transformers import SentenceTransformer
    # Reuse the unchanged P5B3 chunk/mean/L2 implementation; no new encoding policy.
    from .embedding_benchmark import encode_existing_policy
    torch.set_num_threads(1)
    encoder = SentenceTransformer(str(path), device="cpu", local_files_only=True,
        trust_remote_code=False, model_kwargs={"use_safetensors": True})
    vectors, metadata = encode_existing_policy(encoder, texts)
    if vectors.shape != (len(candidates), identity.dimension) or metadata["text_policy"] != identity.text_policy:
        raise ValueError("Candidate encoder policy mismatch")
    metadata["embedding_text_fingerprint"] = model.embedding_text_fingerprint
    return vectors, metadata


def recall_candidates(model_path, cache_dir, candidates):
    """Local callable runtime slice; the sole interest-state read is model_path."""
    _candidate_ids([record["id"] for record in candidates])
    model = load_center_model(model_path)
    vectors, metadata = encode_candidates(model, cache_dir, candidates)
    results = CenterRecallIndex(model).recall([record["id"] for record in candidates], vectors,
        embedding_text_fingerprint=metadata["embedding_text_fingerprint"])
    return {"interest_model_revision": model.interest_model_revision, "encoding": metadata, "results": results}
