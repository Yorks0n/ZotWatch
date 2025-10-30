from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, validator


class ZoteroApiConfig(BaseModel):
    user_id: str = Field(..., alias="user_id")
    api_key_env: str = Field("ZOTERO_API_KEY", alias="api_key_env")
    page_size: int = 100
    polite_delay_ms: int = 200

    def api_key(self) -> str:
        key = os.getenv(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"Environment variable '{self.api_key_env}' is required for Zotero API access."
            )
        return key


class ZoteroConfig(BaseModel):
    mode: str = "api"
    api: ZoteroApiConfig = Field(default_factory=ZoteroApiConfig)

    @validator("mode")
    def validate_mode(cls, value: str) -> str:
        allowed = {"api", "bbt"}
        if value not in allowed:
            raise ValueError(f"Unsupported Zotero mode '{value}'. Allowed: {sorted(allowed)}")
        return value


class AltmetricConfig(BaseModel):
    enabled: bool = False
    api_key_env: Optional[str] = None

    def api_key(self) -> Optional[str]:
        if not self.enabled or not self.api_key_env:
            return None
        return os.getenv(self.api_key_env)


class OpenAlexConfig(BaseModel):
    enabled: bool = True
    mailto: str = "you@example.com"


class CrossRefConfig(BaseModel):
    enabled: bool = True
    mailto: str = "you@example.com"


class ArxivConfig(BaseModel):
    enabled: bool = True
    categories: List[str] = Field(default_factory=lambda: ["cs.LG"])


class BioRxivConfig(BaseModel):
    enabled: bool = True
    from_days_ago: int = 30


class MedRxivConfig(BaseModel):
    enabled: bool = False
    from_days_ago: int = 30


class SourcesConfig(BaseModel):
    window_days: int = 30
    openalex: OpenAlexConfig = Field(default_factory=OpenAlexConfig)
    crossref: CrossRefConfig = Field(default_factory=CrossRefConfig)
    arxiv: ArxivConfig = Field(default_factory=ArxivConfig)
    biorxiv: BioRxivConfig = Field(default_factory=BioRxivConfig)
    medrxiv: MedRxivConfig = Field(default_factory=MedRxivConfig)
    altmetric: AltmetricConfig = Field(default_factory=AltmetricConfig)


class ScoreWeights(BaseModel):
    similarity: float = 0.45
    recency: float = 0.15
    citations: float = 0.15
    altmetric: float = 0.10
    journal_quality: float = 0.08
    author_bonus: float = 0.02
    venue_bonus: float = 0.05

    def normalized(self) -> "ScoreWeights":
        total = sum(self.dict().values())
        if not total:
            raise ValueError("Score weights sum to zero; at least one positive weight is required.")
        normalized = {k: v / total for k, v in self.dict().items()}
        return ScoreWeights(**normalized)


class Thresholds(BaseModel):
    must_read: float = 0.75
    consider: float = 0.5


class ScoringConfig(BaseModel):
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    decay_days: Dict[str, int] = Field(
        default_factory=lambda: {"fast": 30, "medium": 60, "slow": 180}
    )
    whitelist_authors: List[str] = Field(default_factory=list)
    whitelist_venues: List[str] = Field(default_factory=list)


class Settings(BaseModel):
    zotero: ZoteroConfig
    sources: SourcesConfig
    scoring: ScoringConfig




def _expand_env_vars(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _expand_env_vars(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_expand_env_vars(item) for item in data]
    if isinstance(data, str):
        return os.path.expandvars(data)
    return data

def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data = _expand_env_vars(data)
    if not isinstance(data, dict):
        raise ValueError(f"Configuration file {path} must contain a mapping at the top level.")
    return data


def load_settings(base_dir: Path | str) -> Settings:
    base = Path(base_dir)
    zotero_cfg = _load_yaml(base / "config" / "zotero.yaml")
    sources_cfg = _load_yaml(base / "config" / "sources.yaml")
    scoring_cfg = _load_yaml(base / "config" / "scoring.yaml")
    return Settings(
        zotero=ZoteroConfig(**zotero_cfg),
        sources=SourcesConfig(**sources_cfg),
        scoring=ScoringConfig(**scoring_cfg),
    )


__all__ = [
    "Settings",
    "load_settings",
    "ZoteroConfig",
    "SourcesConfig",
    "ScoringConfig",
]
