"""Small fixed-model local benchmark; never publishes state or calls an LLM."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

TEXT_POLICY = "title-abstract-token-windows-128-roundtrip-safe-mean-v2"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    identifier: str
    revision: str
    dimension: int
    role: str


MODELS = (
    ModelSpec("minilm-baseline", "sentence-transformers/all-MiniLM-L6-v2",
        "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", 384, "baseline"),
    ModelSpec("multilingual-minilm", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "e8f8c211226b894fcb81acc59f3b34ba3efd5f42", 384, "multilingual challenger"),
    ModelSpec("scientific-specter", "sentence-transformers/allenai-specter",
        "2c68eeca61259b2dd70c3f2628219f925df7031a", 768, "scientific title+abstract challenger"),
)


def snapshot_path(cache_dir, spec):
    return Path(cache_dir) / ("models--" + spec.identifier.replace("/", "--")) / "snapshots" / spec.revision


def encode_existing_policy(model, texts, *, batch_size=32, progress=None):
    """Existing all-token window policy, with a uniform roundtrip overflow guard.

    All models use their released tokenizer/pooling, capped 128-content-token
    windows, normalized chunk vectors, equal-window mean and final normalization.
    In particular this probes SPECTER under ZotWatch's existing chunk policy,
    not native full-paper/512-token SPECTER with a model-specific tuning pass.
    """
    tokenizer = model.tokenizer
    width = min(128, int(model.max_seq_length) - tokenizer.num_special_tokens_to_add())
    if width < 1 or batch_size < 1:
        raise ValueError("Invalid encoding budget")
    chunks, owners, counts = [], [], []
    split_count = 0
    def safe_parts(tokens):
        nonlocal split_count
        decoded = tokenizer.decode(tokens, skip_special_tokens=True)
        if len(tokenizer.encode(decoded, add_special_tokens=True, truncation=False)) <= model.max_seq_length:
            return [decoded]
        if len(tokens) < 2:
            raise ValueError("A single-token roundtrip exceeds the encoding budget")
        split_count += 1
        middle = len(tokens) // 2
        return safe_parts(tokens[:middle]) + safe_parts(tokens[middle:])
    for row, text in enumerate(texts):
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            raise ValueError("Invalid benchmark semantic text")
        tokens = tokenizer.encode(text, add_special_tokens=False, truncation=False)
        if not tokens:
            raise ValueError("Empty benchmark token sequence")
        parts = [part for i in range(0, len(tokens), width) for part in safe_parts(tokens[i:i+width])]
        chunks.extend(parts)
        owners.extend([row] * len(parts))
        counts.append(len(parts))
    sums = np.zeros((len(texts), model.get_sentence_embedding_dimension()), dtype=np.float64)
    for start in range(0, len(chunks), batch_size):
        encoded = np.asarray(model.encode(chunks[start:start+batch_size], batch_size=batch_size,
            show_progress_bar=False, convert_to_numpy=True), dtype=np.float32)
        norms = np.linalg.norm(encoded, axis=1, keepdims=True)
        if not np.isfinite(encoded).all() or np.any(norms <= 1e-12):
            raise ValueError("Invalid benchmark chunk embedding")
        encoded = encoded / (norms + 1e-12)
        np.add.at(sums, owners[start:start+len(encoded)], encoded.astype(np.float64))
        if progress is not None:
            progress(min(start+len(encoded), len(chunks)), len(chunks))
    sums /= np.asarray(counts)[:, None] if counts else 1
    norms = np.linalg.norm(sums, axis=1, keepdims=True)
    if len(sums) and (not np.isfinite(sums).all() or np.any(norms <= 1e-12)):
        raise ValueError("Invalid benchmark record embedding")
    return sums / norms if len(sums) else sums, {"text_policy": TEXT_POLICY,
        "content_window_tokens": width, "model_max_sequence_length": int(model.max_seq_length),
        "batch_size": batch_size, "record_count": len(texts), "chunk_count": len(chunks), "roundtrip_split_count": split_count,
        "chunks_per_record_min": min(counts) if counts else 0, "chunks_per_record_max": max(counts) if counts else 0}
