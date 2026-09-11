from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import List

import numpy as np

from .computational_state import (
    StateExpectation,
    StateLease,
    StateManager,
    descriptor_for_vectorizer,
    expectation_from_snapshot,
)
from .models import ProfileArtifacts, ZoteroItem
from .settings import Settings
from .storage import ProfileStorage
from .utils import utc_now
from .vectorizer import TextVectorizer

logger = logging.getLogger(__name__)


class ProfileBuilder:
    def __init__(
        self,
        base_dir: Path | str,
        storage: ProfileStorage,
        settings: Settings,
        vectorizer: TextVectorizer | None = None,
        *,
        state_dir: Path | None = None,
        state_manager: StateManager | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.storage = storage
        self.settings = settings
        self.vectorizer = vectorizer or TextVectorizer()
        self.state_dir = Path(state_dir) if state_dir is not None else self.base_dir / "data"
        self.state_manager = state_manager or StateManager(self.state_dir)
        self.artifacts = ProfileArtifacts(
            sqlite_path=str(storage.path),
            faiss_path=str(self.state_dir / "faiss.index"),
            profile_json_path=str(self.state_dir / "profile.json"),
        )

    def run(self, *, lease: StateLease | None = None) -> ProfileArtifacts:
        if next(iter(self.storage.iter_items()), None) is None:
            raise RuntimeError("No items found in storage; run ingest before building profile.")
        snapshot = self.storage.read_profile_snapshot()
        items = list(snapshot.items)
        if not items:
            raise RuntimeError("No items found in storage; run ingest before building profile.")

        descriptor = descriptor_for_vectorizer(self.vectorizer)
        logger.info("Vectorizing %d library items", len(items))
        texts = [item.content_for_embedding() for item in items]
        vectors = self.vectorizer.encode(texts)
        profile_summary = self._summarize(items, vectors)
        expectation = expectation_from_snapshot(snapshot, descriptor)

        def verify_current() -> StateExpectation:
            current = self.storage.read_profile_snapshot()
            return expectation_from_snapshot(current, descriptor)

        handle = self.state_manager.publish_generation(
            expectation=expectation,
            vectors=vectors,
            keys=[item.key for item in items],
            profile=profile_summary,
            verify_current=verify_current,
            lease=lease,
            created_at=profile_summary["generated_at"],
        )
        try:
            self.storage.set_embeddings(
                (item.key, vector.tobytes()) for item, vector in zip(items, vectors)
            )
        except Exception as exc:  # legacy cache is not authoritative E4 state
            logger.warning("Failed to update legacy SQLite embedding cache: %s", exc)

        self.artifacts = ProfileArtifacts(
            sqlite_path=str(self.storage.path),
            faiss_path=str(handle.index_path),
            profile_json_path=str(handle.profile_path),
            manifest_path=str(handle.manifest_path),
            embeddings_path=str(handle.embeddings_path),
            generation_id=handle.generation_id,
        )
        logger.info("Published computational state generation %s", handle.generation_id)
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
