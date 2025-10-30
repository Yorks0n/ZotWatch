from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import List

import numpy as np

from .faiss_store import FaissIndex
from .models import ProfileArtifacts, ZoteroItem
from .settings import Settings
from .storage import ProfileStorage
from .utils import json_dumps, utc_now
from .vectorizer import TextVectorizer

logger = logging.getLogger(__name__)


class ProfileBuilder:
    def __init__(
        self,
        base_dir: Path | str,
        storage: ProfileStorage,
        settings: Settings,
        vectorizer: TextVectorizer | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.storage = storage
        self.settings = settings
        self.vectorizer = vectorizer or TextVectorizer()
        self.artifacts = ProfileArtifacts(
            sqlite_path=str(self.base_dir / "data" / "profile.sqlite"),
            faiss_path=str(self.base_dir / "data" / "faiss.index"),
            profile_json_path=str(self.base_dir / "data" / "profile.json"),
        )

    def run(self) -> ProfileArtifacts:
        items = list(self.storage.iter_items())
        if not items:
            raise RuntimeError("No items found in storage; run ingest before building profile.")

        logger.info("Vectorizing %d library items", len(items))
        texts = [item.content_for_embedding() for item in items]
        vectors = self.vectorizer.encode(texts)

        for item, vector in zip(items, vectors):
            self.storage.set_embedding(item.key, vector.tobytes())

        logger.info("Building FAISS index")
        index, order = FaissIndex.from_vectors(vectors)
        index.save(self.artifacts.faiss_path)

        profile_summary = self._summarize(items, vectors)
        json_path = Path(self.artifacts.profile_json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json_dumps(profile_summary, indent=2), encoding="utf-8")
        logger.info("Wrote profile summary to %s", json_path)
        return self.artifacts

    def _summarize(self, items: List[ZoteroItem], vectors: np.ndarray) -> dict:
        authors = Counter()
        venues = Counter()
        for item in items:
            authors.update(item.creators)
            venue = item.raw.get("data", {}).get("publicationTitle")
            if venue:
                venues.update([venue])

        centroid = np.mean(vectors, axis=0)
        centroid = centroid / (np.linalg.norm(centroid) + 1e-12)

        top_authors = [{"author": k, "count": v} for k, v in authors.most_common(20)]
        top_venues = [{"venue": k, "count": v} for k, v in venues.most_common(20)]

        return {
            "generated_at": utc_now().isoformat(),
            "item_count": len(items),
            "model": self.vectorizer.model_name,
            "centroid": centroid.tolist(),
            "top_authors": top_authors,
            "top_venues": top_venues,
        }


__all__ = ["ProfileBuilder"]
