"""Private P5B5/P5C2 v3 evidence; accepted P5C/topic-result-v2 stays unchanged."""
from typing import Literal

from pydantic import Field, model_validator

from .contract import ClosedModel, UUID, SHA
from .results import Evidence, TopicRecommendation, TopicRunResult


class RecallMetadata(ClosedModel):
    interest_model_revision: str = Field(pattern=r"^[0-9a-f]{32}$")
    embedding_text_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    recall_policy: Literal["center-recall-v1"] = "center-recall-v1"
    threshold: Literal[0.55] = 0.55


class CenterMatch(ClosedModel):
    interest_id: UUID
    center_cosine: float = Field(ge=0.55, le=1.)


class RecallDecision(RecallMetadata):
    candidate_id: str = Field(min_length=1)
    recalled: bool
    best_match: CenterMatch | None
    secondary_matches: list[CenterMatch] = Field(max_length=3)
    qualification: Literal["recalled", "below_threshold", "insufficient_semantic_text"]

    @model_validator(mode="after")
    def qualified(self):
        if self.recalled != (self.best_match is not None) or self.recalled != (self.qualification == "recalled"):
            raise ValueError("Recall qualification disagrees")
        if not self.recalled and self.secondary_matches:
            raise ValueError("Rejected candidate has matches")
        return self


class IntegrationEvidence(Evidence):
    # profile_blob_sha is the exact confirmed revision; keep the existing commit too.
    latent_recall: RecallMetadata | None = None
    recall_decisions: list[RecallDecision] = Field(default_factory=list, max_length=2000)


class IntegrationRecommendation(TopicRecommendation):
    latent_recall: RecallDecision
    confirmed_profile_revision: SHA

    @model_validator(mode="after")
    def admission(self):
        if not self.latent_recall.recalled or self.latent_recall.candidate_id != self.work_key:
            raise ValueError("Recommendation lacks recall admission")
        return self


class IntegrationRunResult(TopicRunResult):
    schema_name: Literal["zotwatch-latent-topic-run-result"] = "zotwatch-latent-topic-run-result"
    schema_version: Literal[3] = 3
    candidate_policy: Literal["center-recall-v1"] = "center-recall-v1"
    evidence: IntegrationEvidence | None = None
    recommendations: list[IntegrationRecommendation] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def provenance(self):
        if self.command == "watch" and self.status == "succeeded" and (
            self.evidence is None or self.evidence.latent_recall is None
        ):
            raise ValueError("Missing latent revision evidence")
        if self.evidence is not None:
            metadata = self.evidence.latent_recall
            rows = self.evidence.recall_decisions
            if len({r.candidate_id for r in rows}) != len(rows):
                raise ValueError("Duplicate recall evidence")
            for row in rows:
                if metadata is None or any(getattr(row, k) != getattr(metadata, k) for k in RecallMetadata.model_fields):
                    raise ValueError("Recall revision provenance disagrees")
            by_key = {r.candidate_id: r for r in rows}
            for item in self.recommendations:
                if item.confirmed_profile_revision != self.evidence.profile_blob_sha or by_key.get(item.work_key) != item.latent_recall:
                    raise ValueError("Recommendation revision provenance disagrees")
        return self
