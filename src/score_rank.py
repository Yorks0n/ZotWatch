from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .computational_state import (
    StateCompatibilityError,
    StateHandle,
    StateLease,
    StateManager,
    descriptor_for_vectorizer,
    expectation_from_snapshot,
)
from .faiss_store import FaissIndex
from .models import CandidateWork, RankedWork
from .settings import Settings
from .storage import ProfileStorage
from .vectorizer import TextVectorizer

logger = logging.getLogger(__name__)


@dataclass
class RankerArtifacts:
    index_path: Path
    profile_path: Path


class WorkRanker:
    def __init__(
        self,
        base_dir: Path | str,
        settings: Settings,
        vectorizer: TextVectorizer | None = None,
        *,
        state_dir: Path | None = None,
        metrics_path: Path | None = None,
        state_handle: StateHandle | None = None,
        state_manager: StateManager | None = None,
        storage: ProfileStorage | None = None,
        lease: StateLease | None = None,
    ):
        self.base_dir = Path(base_dir)
        self.settings = settings
        self.vectorizer = vectorizer or TextVectorizer()
        self.state_dir = Path(state_dir) if state_dir is not None else self.base_dir / "data"
        self.metrics_path = Path(metrics_path) if metrics_path is not None else self.base_dir / "data" / "journal_metrics.csv"
        descriptor = descriptor_for_vectorizer(self.vectorizer)
        if state_handle is None:
            manager = state_manager or StateManager(self.state_dir)
            owned_storage = storage is None
            storage = storage or ProfileStorage(self.state_dir / "profile.sqlite")
            try:
                with manager.lease_scope(lease):
                    try:
                        snapshot = storage.read_profile_snapshot()
                    except Exception as exc:
                        raise StateCompatibilityError(
                            "A committed profile SQLite mirror is required for ranking"
                        ) from exc
                    expectation = expectation_from_snapshot(snapshot, descriptor)
                    state_handle = manager.load_current(expectation)
            finally:
                if owned_storage:
                    storage.close()
        if (
            state_handle.manifest.embedding.model_fingerprint_sha256
            != descriptor.hard_fingerprint_sha256
        ):
            raise StateCompatibilityError(
                "Current state was built by an incompatible embedding model"
            )
        if (
            descriptor.dimension is not None
            and state_handle.manifest.embedding.dimension != descriptor.dimension
        ):
            raise StateCompatibilityError(
                "Current state has an incompatible embedding dimension"
            )
        self.artifacts = RankerArtifacts(
            index_path=state_handle.index_path,
            profile_path=state_handle.profile_path,
        )
        self.index = FaissIndex.load(self.artifacts.index_path)
        self.profile = state_handle.profile
        self.state_handle = state_handle
        self.journal_metrics = self._load_journal_metrics()

    def _load_profile(self) -> dict:
        path = self.artifacts.profile_path
        if not path.exists():
            raise FileNotFoundError("Profile JSON not found; run profile build first.")
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_journal_metrics(self) -> Dict[str, float]:
        path = self.metrics_path
        metrics: Dict[str, float] = {}
        if not path.exists():
            logger.warning("Journal metrics file not found: %s", path)
            return metrics
        try:
            with path.open("r", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    title = (row.get("title") or "").strip().lower()
                    sjr = row.get("sjr")
                    if not title or not sjr:
                        continue
                    try:
                        metrics[title] = float(sjr)
                    except ValueError:
                        continue
        except Exception as exc:
            logger.warning("Failed to load journal metrics: %s", exc)
            return {}
        logger.info("Loaded %d journal SJR entries", len(metrics))
        return metrics

    def rank(self, candidates: List[CandidateWork]) -> List[RankedWork]:
        if not candidates:
            return []

        texts = [c.content_for_embedding() for c in candidates]
        vectors = self.vectorizer.encode(texts)
        if vectors.ndim != 2 or vectors.shape[1] != self.state_handle.manifest.embedding.dimension:
            raise StateCompatibilityError(
                "Candidate embedding dimension does not match current computational state"
            )
        logger.info("Scoring %d candidate works", len(candidates))

        distances, _ = self.index.search(vectors, top_k=1)
        weights = self.settings.scoring.weights
        thresholds = self.settings.scoring.thresholds

        ranked: List[RankedWork] = []
        for candidate, vector, distance in zip(candidates, vectors, distances):
            similarity = float(distance[0]) if distance.size else 0.0
            recency_score = _compute_recency(candidate.published, self.settings)
            citation_score, altmetric_score = _compute_metric(candidate)
            journal_quality, journal_sjr = _journal_quality_score(candidate.venue, self.journal_metrics)
            author_bonus = _bonus(candidate.authors, self.settings.scoring.whitelist_authors)
            venue_bonus = _bonus(
                [candidate.venue] if candidate.venue else [],
                self.settings.scoring.whitelist_venues,
            )

            score = (
                similarity * weights.similarity
                + recency_score * weights.recency
                + citation_score * weights.citations
                + altmetric_score * weights.altmetric
                + journal_quality * getattr(weights, "journal_quality", 0.0)
                + author_bonus * weights.author_bonus
                + venue_bonus * weights.venue_bonus
            )

            label = "ignore"
            if score >= thresholds.must_read:
                label = "must_read"
            elif score >= thresholds.consider:
                label = "consider"

            ranked.append(
                RankedWork(
                    **candidate.dict(),
                    score=score,
                    similarity=similarity,
                    recency_score=recency_score,
                    metric_score=citation_score,
                    altmetric_score=altmetric_score,
                    author_bonus=author_bonus,
                    venue_bonus=venue_bonus,
                    journal_quality=journal_quality,
                    journal_sjr=journal_sjr,
                    label=label,
                )
            )
        ranked.sort(key=lambda w: w.score, reverse=True)
        return ranked


def _bonus(values: List[str], whitelist: List[str]) -> float:
    whitelist_lower = {v.lower() for v in whitelist}
    for value in values:
        if value and value.lower() in whitelist_lower:
            return 1.0
    return 0.0


def _journal_quality_score(venue: Optional[str], metrics: Dict[str, float]) -> Tuple[float, Optional[float]]:
    if not venue:
        return 1.0, None
    key = venue.strip().lower()
    value = metrics.get(key)
    if value is None:
        return 1.0, None
    score = float(np.log1p(value))
    if score < 1.0:
        score = 1.0
    return score, float(value)


def _compute_recency(published: datetime | None, settings: Settings) -> float:
    if not published:
        return 0.0
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta_days = max((now - published).days, 0)
    decay = settings.scoring.decay_days
    if delta_days <= decay.get("fast", 30):
        return 1.0
    if delta_days <= decay.get("medium", 60):
        return 0.7
    if delta_days <= decay.get("slow", 180):
        return 0.4
    return 0.1


def _compute_metric(candidate: CandidateWork) -> Tuple[float, float]:
    citations = float(candidate.metrics.get("cited_by", candidate.metrics.get("is-referenced-by", 0.0)))
    altmetric = float(candidate.metrics.get("altmetric", 0.0))
    citation_score = float(np.log1p(citations)) if citations else 0.0
    altmetric_score = float(np.log1p(altmetric)) if altmetric else 0.0
    return citation_score, altmetric_score


__all__ = ["WorkRanker"]
