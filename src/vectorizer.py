from __future__ import annotations

import logging
from typing import Iterable, List

import numpy as np

logger = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - handled via runtime requirement
    SentenceTransformer = None  # type: ignore


class TextVectorizer:
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        if SentenceTransformer is None:
            raise RuntimeError(
                "sentence-transformers is not installed. Install it or adjust requirements."
            )
        logger.info("Loading embedding model %s", self.model_name)
        self._model = SentenceTransformer(self.model_name)

    @property
    def model(self):  # type: ignore
        self.load()
        return self._model

    def encode(self, texts: Iterable[str]) -> np.ndarray:
        self.load()
        embeddings = self.model.encode(list(texts), show_progress_bar=False)
        embeddings = np.asarray(embeddings, dtype=np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-12
        return embeddings / norms

    def encode_single(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


__all__ = ["TextVectorizer"]
