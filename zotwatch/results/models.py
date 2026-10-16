from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScoreComponent(ContractModel):
    raw_value: float | None
    weighted_contribution: float
    input_available: bool


class RecommendationItem(ContractModel):
    rank: int = Field(ge=1)
    work_key: str = Field(min_length=1, max_length=512)
    public_id: str | None = None
    source: str
    identifier: str
    title: str
    abstract: str | None = None
    authors: list[str]
    doi: str | None = None
    url: str | None = None
    published: str | None = None
    venue: str | None = None
    is_preprint: bool | None = None
    label: Literal["must_read", "consider", "ignore"]
    score: float
    score_breakdown: dict[str, ScoreComponent]

    @model_validator(mode="after")
    def additive_score(self) -> "RecommendationItem":
        expected = (
            "similarity", "recency", "citations", "altmetric", "journal_quality",
            "author_bonus", "venue_bonus",
        )
        if tuple(self.score_breakdown) != expected:
            raise ValueError("score_breakdown must contain the seven ordered legacy components")
        total = sum(item.weighted_contribution for item in self.score_breakdown.values())
        if abs(total - self.score) > 1e-9:
            raise ValueError("score must equal the additive weighted contributions")
        return self


class RecommendationDocument(ContractModel):
    schema_name: Literal["zotwatch-recommendations"] = "zotwatch-recommendations"
    schema_version: Literal[1] = 1
    run_id: str
    generated_at: str
    recommendations: list[RecommendationItem]


class ArtifactReference(ContractModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    publishable: bool

    @field_validator("path")
    @classmethod
    def relative_safe_path(cls, value: str) -> str:
        from pathlib import PurePosixPath
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value in {"", "."}:
            raise ValueError("artifact path must be a safe relative path")
        return value


StageStatus = Literal["pending", "running", "succeeded", "skipped", "degraded", "failed"]
RunStatus = Literal["succeeded", "degraded", "failed"]


class RunError(ContractModel):
    code: str
    message: str
    diagnostic_id: str


class StageRecord(ContractModel):
    stage: Literal[
        "config_validation", "credential_preflight", "zotero_sync",
        "computational_state", "candidate_fetch", "dedupe", "ranking",
        "output_render", "zotero_writeback", "output_publish",
    ]
    status: StageStatus
    started_at: str | None = None
    finished_at: str | None = None
    count: int | None = None
    error: RunError | None = None


class RunManifest(ContractModel):
    schema_name: Literal["zotwatch-run-manifest"] = "zotwatch-run-manifest"
    schema_version: Literal[1] = 1
    run_id: str
    command: Literal["profile", "watch"]
    status: RunStatus
    exit_code: Literal[0, 2, 3, 4, 5]
    generated_at: str
    config_schema_version: int | None
    config_fingerprint_sha256: str | None
    state_generation_id: str | None = None
    output_generation_id: str | None = None
    stages: list[StageRecord]
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    error: RunError | None = None


class RunResult(ContractModel):
    schema_name: Literal["zotwatch-run-result"] = "zotwatch-run-result"
    schema_version: Literal[1] = 1
    run_id: str
    status: RunStatus
    exit_code: Literal[0, 2, 3, 4, 5]
    error: RunError | None = None
    manifest_path: str | None = None
    state_generation_id: str | None = None
    output_generation_id: str | None = None
    artifacts: list[ArtifactReference] = Field(default_factory=list)

    @field_validator("manifest_path")
    @classmethod
    def safe_manifest_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        ArtifactReference.relative_safe_path(value)
        return value


__all__ = [
    "ArtifactReference", "RecommendationDocument", "RecommendationItem", "RunError",
    "RunManifest", "RunResult", "ScoreComponent", "StageRecord",
]
