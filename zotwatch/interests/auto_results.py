"""Private latent-auto v1 results, independent of confirmed interests."""
from typing import Literal
from pydantic import Field, model_validator
from .contract import ClosedModel, UUID
from .integration_results import RecallMetadata


class AutoEvidence(RecallMetadata):
    workspace_repository_id: int = Field(gt=0)
    library_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    encoder_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    formal_center_ids: list[UUID] = Field(min_length=1)

    @model_validator(mode="after")
    def encoder(self):
        if self.encoder_fingerprint != self.embedding_text_fingerprint or len(set(self.formal_center_ids)) != len(self.formal_center_ids):
            raise ValueError("Invalid encoder/center evidence")
        return self


class AutoRecommendation(ClosedModel):
    work_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str | None
    primary_center_id: UUID
    center_cosine: float = Field(ge=.55, le=1.)
    score: float = Field(ge=.55, le=1.)
    source: str
    identifier: str
    doi: str | None
    venue: str | None
    published: str

    @model_validator(mode="after")
    def cosine(self):
        if self.score != self.center_cosine:
            raise ValueError("Score must be the maximum center cosine")
        return self


class AutoRunResult(ClosedModel):
    schema_name: Literal["zotwatch-latent-auto-run-result"] = "zotwatch-latent-auto-run-result"
    schema_version: Literal[1] = 1
    ranking_policy: Literal["latent-auto-v1"] = "latent-auto-v1"
    candidate_policy: Literal["center-recall-v1"] = "center-recall-v1"
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    command: Literal["profile", "watch"]
    status: Literal["succeeded", "degraded", "failed", "not_ready"]
    exit_code: Literal[0, 3, 4, 5]
    reason: str = Field(pattern=r"^[A-Z_]+$")
    evidence: AutoEvidence | None = None
    state_generation_id: str | None = None
    recommendations: list[AutoRecommendation] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def outcome(self):
        expected = {"succeeded": 0, "not_ready": 0, "degraded": 5}
        if self.status in expected and self.exit_code != expected[self.status] or self.status == "failed" and self.exit_code == 0:
            raise ValueError("Invalid exit semantics")
        if (self.status != "succeeded" or self.command != "watch") and self.recommendations:
            raise ValueError("Only successful watch can recommend")
        if self.status == "succeeded" and self.evidence is None:
            raise ValueError("Missing own-model evidence")
        if len({r.work_key for r in self.recommendations}) != len(self.recommendations):
            raise ValueError("Duplicate work identity")
        if any(r.primary_center_id not in self.evidence.formal_center_ids for r in self.recommendations):
            raise ValueError("Unknown primary center")
        return self


def result_filename(value):
    return "latent-auto-result-v1.json" if value.schema_name == "zotwatch-latent-auto-run-result" else f"topic-result-v{value.schema_version}.json"
