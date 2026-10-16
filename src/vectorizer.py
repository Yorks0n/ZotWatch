from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, List

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# Reviewed from the upstream Hugging Face repository on 2026-09-14.
DEFAULT_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - handled via runtime requirement
    SentenceTransformer = None  # type: ignore


class TextVectorizer:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        *,
        model_revision: str | None = None,
        artifact_identity: str | None = None,
    ):
        self.model_name = model_name
        self.model_revision = (
            DEFAULT_MODEL_REVISION
            if model_revision is None and model_name == DEFAULT_MODEL_NAME
            else model_revision
        )
        self.artifact_identity = artifact_identity
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        if SentenceTransformer is None:
            raise RuntimeError(
                "sentence-transformers is not installed. Install it or adjust requirements."
            )
        logger.info("Loading embedding model %s", self.model_name)
        kwargs = {"revision": self.model_revision} if self.model_revision else {}
        self._model = SentenceTransformer(self.model_name, **kwargs)

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

    def runtime_descriptor(self):
        """Return stable, offline compatibility metadata for the loaded model."""
        from .computational_state import EmbeddingRuntimeDescriptor

        self.load()
        revision = self.model_revision or _cached_huggingface_revision(self.model_name)
        artifact_identity = self.artifact_identity
        if artifact_identity is None:
            artifact_identity = (
                f"huggingface-snapshot:{revision}"
                if revision
                else f"zotwatch-model-contract:{self.model_name}:v1"
            )
        return EmbeddingRuntimeDescriptor(
            provider="local",
            model_identifier=self.model_name,
            model_revision=revision,
            artifact_identity=artifact_identity,
            dimension=int(self.model.get_sentence_embedding_dimension()),
        )


def _cached_huggingface_revision(model_name: str) -> str | None:
    """Read the standard local cache ref without making a network call."""
    configured = os.getenv("HUGGINGFACE_HUB_CACHE")
    if configured:
        hub_root = Path(configured).expanduser()
    else:
        hf_home = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface"))
        hub_root = hf_home / "hub"
    repo = hub_root / ("models--" + model_name.replace("/", "--"))
    reference = repo / "refs" / "main"
    try:
        revision = reference.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if not revision or not (repo / "snapshots" / revision).is_dir():
        return None
    return revision


__all__ = ["DEFAULT_MODEL_NAME", "DEFAULT_MODEL_REVISION", "TextVectorizer"]
