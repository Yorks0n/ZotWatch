from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256

from src.models import RankedWork
from src.settings import ScoreWeights

from .models import RecommendationDocument, RecommendationItem, ScoreComponent


def _component(raw: float | None, weight: float, available: bool) -> ScoreComponent:
    return ScoreComponent(
        raw_value=raw,
        weighted_contribution=(float(raw) * float(weight)) if raw is not None else 0.0,
        input_available=available,
    )


def _work_key(work: RankedWork) -> str:
    stable = work.doi or f"{work.source}:{work.identifier}"
    return sha256(stable.strip().lower().encode("utf-8")).hexdigest()


def project_recommendations(
    works: list[RankedWork],
    weights: ScoreWeights,
    *,
    run_id: str,
    generated_at: str,
) -> RecommendationDocument:
    projected = []
    for rank, work in enumerate(works, start=1):
        cited_available = any(key in work.metrics for key in ("cited_by", "is-referenced-by"))
        alt_available = "altmetric" in work.metrics
        breakdown = OrderedDict([
            ("similarity", _component(work.similarity, weights.similarity, True)),
            ("recency", _component(work.recency_score, weights.recency, work.published is not None)),
            ("citations", _component(work.metric_score, weights.citations, cited_available)),
            ("altmetric", _component(work.altmetric_score, weights.altmetric, alt_available)),
            ("journal_quality", _component(work.journal_quality, getattr(weights, "journal_quality", 0.0), work.journal_sjr is not None)),
            ("author_bonus", _component(work.author_bonus, weights.author_bonus, bool(work.authors))),
            ("venue_bonus", _component(work.venue_bonus, weights.venue_bonus, work.venue is not None)),
        ])
        computed_score = sum(item.weighted_contribution for item in breakdown.values())
        if abs(computed_score - work.score) > 1e-9:
            raise ValueError("ranked score does not match its additive provenance")
        projected.append(RecommendationItem(
            rank=rank,
            work_key=_work_key(work),
            public_id=(str(work.extra["public_id"]) if work.extra.get("public_id") is not None else None),
            source=work.source,
            identifier=work.identifier,
            title=work.title,
            abstract=work.abstract,
            authors=list(work.authors),
            doi=work.doi,
            url=work.url,
            published=work.published.isoformat() if work.published else None,
            venue=work.venue,
            is_preprint=work.is_preprint,
            label=work.label,
            score=work.score,
            score_breakdown=breakdown,
        ))
    return RecommendationDocument(run_id=run_id, generated_at=generated_at, recommendations=projected)


__all__ = ["project_recommendations"]
