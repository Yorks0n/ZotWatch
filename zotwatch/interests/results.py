"""Closed private v2 result; deliberately excluded from public E5 artifacts."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contract import ClosedModel, SHA, UUID, InterestError


class Evidence(ClosedModel):
    feedback_commit_sha: SHA
    profile_blob_sha: SHA
    semantic_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    text_policy: Literal["topic-token-windows-128-mean-v1"] = "topic-token-windows-128-mean-v1"


class TopicRecommendation(ClosedModel):
    work_key: str
    title: str
    url: str | None
    matched_interest_id: UUID
    raw_similarity: float = Field(ge=0.35, le=1)
    priority_multiplier: Literal[0.8, 1.0, 1.2]
    horizon_multiplier: Literal[1.0, 1.1]
    topic_relevance: float
    author_bonus: Literal[0.0, 0.02]
    venue_bonus: Literal[0.0, 0.05]
    preference_bonus: float = Field(ge=0, le=0.05)
    score: float

    @model_validator(mode="after")
    def arithmetic(self):
        values = (
            (self.topic_relevance, self.raw_similarity * self.priority_multiplier * self.horizon_multiplier),
            (self.preference_bonus, min(0.05, self.author_bonus + self.venue_bonus)),
            (self.score, self.topic_relevance + self.preference_bonus),
        )
        if any(abs(a - b) > 1e-9 for a, b in values):
            raise ValueError("Topic score provenance disagrees")
        return self


class TopicRunResult(ClosedModel):
    schema_name: Literal["zotwatch-topic-run-result"] = "zotwatch-topic-run-result"
    schema_version: Literal[2] = 2
    ranking_policy: Literal["topic-v1"] = "topic-v1"
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    command: Literal["profile", "watch"]
    status: Literal["succeeded", "degraded", "failed", "not_ready", "paused"]
    exit_code: Literal[0, 3, 4, 5]
    reason: str = Field(pattern=r"^[A-Z_]+$")
    evidence: Evidence | None = None
    state_generation_id: str | None = None
    recommendations: list[TopicRecommendation] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def outcome(self):
        expected = {"succeeded": 0, "not_ready": 0, "paused": 0, "degraded": 5}
        if self.status in expected and self.exit_code != expected[self.status]:
            raise ValueError("Invalid exit semantics")
        if self.status == "failed" and self.exit_code == 0:
            raise ValueError("Invalid failed exit")
        if self.status != "succeeded" and self.recommendations:
            raise ValueError("Non-successful run cannot release recommendations")
        if self.command == "profile" and self.recommendations:
            raise ValueError("Profile cannot recommend")
        if self.command == "watch" and self.status == "succeeded" and (self.evidence is None or self.evidence.model_fingerprint is None):
            raise ValueError("Missing confirmed input evidence")
        return self


def parse_result(content: bytes) -> TopicRunResult:
    try:
        if len(content) > 2 * 1024 * 1024:
            raise ValueError()
        return TopicRunResult.model_validate_json(content)
    except ValueError:
        raise InterestError("INTEREST_RESULT_INVALID") from None


def materialize(machine: Path, process_exit: int, state: Path, destination: Path):
    try:
        content = machine.read_bytes()
        if len(content) > 2 * 1024 * 1024:
            raise ValueError()
        value = parse_result(content)
        recorded = state / "runs" / f"topic-{value.run_id}.json"
        if recorded.is_symlink() or recorded.read_bytes() != content or value.exit_code != process_exit:
            raise ValueError()
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "topic-result-v2.json").write_bytes(content)
        return value
    except (OSError, ValueError):
        raise InterestError("INTEREST_RESULT_INVALID") from None
