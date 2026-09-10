from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


ServiceId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,47}$")]
ModelIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$"),
]
CustomConnectionId = str


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ZoteroConfigV2(StrictConfigModel):
    library_type: Literal["user"]


class CandidatesConfigV2(StrictConfigModel):
    provider: Literal["public-api-v1"]
    sources: list[Literal["crossref", "arxiv", "biorxiv", "medrxiv", "openalex"]] = Field(
        min_length=1
    )
    window_days: Literal[7]

    @field_validator("sources")
    @classmethod
    def sources_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("candidate sources must be unique")
        return value


class RankingConfigV2(StrictConfigModel):
    policy: Literal["legacy-v1"]
    top_n: int = Field(ge=1, le=200)
    max_preprint_ratio: Literal[0.3]


class EmbeddingConfigV2(StrictConfigModel):
    provider: Literal["local"]
    model: Literal["sentence-transformers/all-MiniLM-L6-v2"]


class FeatureBudgetConfig(StrictConfigModel):
    max_items: int = Field(default=20, ge=1, le=200)
    max_requests: int = Field(default=5, ge=1, le=20)
    max_input_tokens: int = Field(default=12000, ge=1, le=100000)
    max_output_tokens: int = Field(default=2000, ge=1, le=32000)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class FeatureRouteConfig(StrictConfigModel):
    enabled: bool
    service: ServiceId | None = None
    budget: FeatureBudgetConfig | None = None

    @model_validator(mode="after")
    def service_matches_enabled_state(self) -> "FeatureRouteConfig":
        if self.enabled and self.service is None:
            raise ValueError("enabled feature requires a service")
        if not self.enabled and self.service is not None:
            raise ValueError("disabled feature cannot select a service")
        return self


class PresetServiceConfig(StrictConfigModel):
    provider: str = Field(min_length=1, max_length=48)
    model: ModelIdentifier


class CustomServiceConfig(StrictConfigModel):
    provider: Literal["custom"]
    protocol: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=9, max_length=2048, pattern=r"^https://[^\s?#]+$", repr=False)
    model: ModelIdentifier
    connection_id: CustomConnectionId


ServiceConfig = Union[CustomServiceConfig, PresetServiceConfig]


class AIFeaturesConfig(StrictConfigModel):
    rerank: FeatureRouteConfig
    summary: FeatureRouteConfig


class AIConfigV2(StrictConfigModel):
    services: dict[ServiceId, ServiceConfig]
    features: AIFeaturesConfig


class OutputsConfigV2(StrictConfigModel):
    formats: list[Literal["rss", "html", "json"]] = Field(min_length=1)
    publish: bool

    @field_validator("formats")
    @classmethod
    def formats_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("output formats must be unique")
        return value


class ZotWatchConfigV2(StrictConfigModel):
    schema_version: Literal[2]
    zotero: ZoteroConfigV2
    candidates: CandidatesConfigV2
    ranking: RankingConfigV2
    embedding: EmbeddingConfigV2
    ai: AIConfigV2
    outputs: OutputsConfigV2


__all__ = [
    "AIConfigV2",
    "AIFeaturesConfig",
    "CandidatesConfigV2",
    "CustomConnectionId",
    "CustomServiceConfig",
    "EmbeddingConfigV2",
    "FeatureBudgetConfig",
    "FeatureRouteConfig",
    "OutputsConfigV2",
    "PresetServiceConfig",
    "RankingConfigV2",
    "ServiceConfig",
    "ZotWatchConfigV2",
    "ZoteroConfigV2",
]
