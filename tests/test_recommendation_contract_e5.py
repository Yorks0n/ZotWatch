import json
import math

import jsonschema

from src.fetch_new import CandidateFetcher
from src.models import CandidateWork, RankedWork
from zotwatch.results.models import RecommendationDocument
from zotwatch.results.projector import project_recommendations
from zotwatch.resources import contract_schema_path


def ranked_work(**changes):
    values = dict(
        source="public-api",
        identifier="work-1",
        title="A useful paper",
        abstract="Safe public abstract",
        authors=["A. Author"],
        doi="10.1000/example",
        url="https://example.invalid/work-1",
        venue="Example Journal",
        metrics={"cited_by": 7.0, "altmetric": 3.0},
        extra={"public_id": "public-1", "private": "must-not-project"},
        is_preprint=True,
        score=0.855,
        similarity=0.5,
        recency_score=1.0,
        metric_score=2.0,
        altmetric_score=1.0,
        author_bonus=1.0,
        venue_bonus=0.0,
        journal_quality=1.5,
        journal_sjr=2.4,
        label="must_read",
    )
    values.update(changes)
    return RankedWork(**values)


def test_recommendation_v1_has_complete_additive_score_provenance(settings):
    work = ranked_work()
    document = project_recommendations(
        [work], settings.scoring.weights, run_id="run-1", generated_at="2026-01-15T12:00:00Z"
    )
    validated = RecommendationDocument.model_validate(document.model_dump(mode="json"))
    with contract_schema_path("recommendations-v1.schema.json") as schema_path:
        jsonschema.validate(validated.model_dump(mode="json"), json.loads(schema_path.read_text()))
    item = validated.recommendations[0]
    assert list(item.score_breakdown) == [
        "similarity", "recency", "citations", "altmetric", "journal_quality",
        "author_bonus", "venue_bonus",
    ]
    assert math.isclose(
        item.score,
        sum(component.weighted_contribution for component in item.score_breakdown.values()),
        rel_tol=0,
        abs_tol=1e-12,
    )
    assert item.public_id == "public-1"
    assert item.is_preprint is True
    assert "private" not in json.dumps(item.model_dump(mode="json"))


def test_public_preprint_metadata_is_explicitly_transported(workspace, settings):
    fetcher = CandidateFetcher(settings, workspace)
    explicit = fetcher._candidate_from_public_api({
        "id": "p1", "source": "crossref", "source_identifier": "x", "title": "Title",
        "is_preprint": False,
    })
    unknown = fetcher._candidate_from_public_api({
        "id": "p2", "source": "unknown", "source_identifier": "y", "title": "Other",
    })
    assert explicit.is_preprint is False
    assert unknown.is_preprint is None
    assert CandidateWork(source="arxiv", identifier="z", title="Z").is_preprint is None
