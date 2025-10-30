from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import faiss
except ImportError:  # pragma: no cover - runtime dependency
    faiss = None  # type: ignore


class FaissIndex:
    def __init__(self, dim: int, index: "faiss.Index" | None = None):  # type: ignore
        if faiss is None:
            raise RuntimeError("faiss is required; install faiss-cpu or adjust configuration.")
        self.dim = dim
        self.index = index or faiss.IndexFlatIP(dim)

    @classmethod
    def from_vectors(cls, vectors: np.ndarray) -> Tuple["FaissIndex", np.ndarray]:
        if vectors.ndim != 2:
            raise ValueError("Vectors must be a 2D array")
        dim = vectors.shape[1]
        instance = cls(dim)
        instance.index.add(vectors)
        return instance, np.arange(vectors.shape[0])

    def save(self, path: Path | str) -> None:
        logger.info("Saving FAISS index to %s", path)
        faiss.write_index(self.index, str(path))

    @classmethod
    def load(cls, path: Path | str) -> "FaissIndex":
        if faiss is None:
            raise RuntimeError("faiss is required; install faiss-cpu or adjust configuration.")
        index = faiss.read_index(str(path))
        if index.ntotal == 0:
            raise ValueError("Loaded FAISS index is empty")
        return cls(index.d, index)

    def search(self, vectors: np.ndarray, top_k: int = 10) -> Tuple[np.ndarray, np.ndarray]:
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        return self.index.search(vectors.astype("float32"), top_k)


__all__ = ["FaissIndex"]
