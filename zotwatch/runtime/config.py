from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path

from src.settings import (
    AltmetricConfig,
    ArxivConfig,
    BioRxivConfig,
    CrossRefConfig,
    MedRxivConfig,
    OpenAlexConfig,
    PublicCandidatesApiConfig,
    ScoringConfig,
    Settings,
    SourcesConfig,
    ZoteroApiConfig,
    ZoteroConfig,
)
from zotwatch.config import ConfigSource, ResolvedFeatureRoute, load_workspace_config
from zotwatch.config.legacy import LEGACY_V1_SCORING_POLICY


_ZOTERO_ID_SLOT = "ZOTERO_USER_ID"
_ZOTERO_KEY_SLOT = "ZOTERO_API_KEY"


@dataclass(frozen=True)
class EffectiveRuntimeConfig:
    source: ConfigSource
    config_schema_version: int | None
    config_fingerprint_sha256: str
    settings: Settings = field(repr=False)
    top_n: int = 50
    max_preprint_ratio: float = 0.3
    output_formats: tuple[str, ...] = ()
    publish_requested: bool = False
    journal_metrics: str = "legacy"
    feature_routes: tuple[ResolvedFeatureRoute, ...] = field(default=(), repr=False)


def _fingerprint(payload: dict) -> str:
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(data).hexdigest()


def _v2_settings(loaded) -> Settings:
    config = loaded.config
    assert config is not None
    connection = loaded.public_candidates
    assert connection is not None
    sources = set(config.candidates.sources)
    return Settings(
        zotero=ZoteroConfig(
            mode="api",
            api=ZoteroApiConfig(
                user_id=os.getenv(_ZOTERO_ID_SLOT, ""),
                api_key_env=_ZOTERO_KEY_SLOT,
            ),
        ),
        sources=SourcesConfig(
            window_days=config.candidates.window_days,
            public_api=PublicCandidatesApiConfig(
                enabled=True,
                base_url=connection.base_url,
                publishable_key=connection.publishable_key,
                page_size=connection.page_size,
                timeout_seconds=connection.timeout_seconds,
            ),
            openalex=OpenAlexConfig(enabled="openalex" in sources),
            crossref=CrossRefConfig(enabled="crossref" in sources),
            arxiv=ArxivConfig(enabled="arxiv" in sources),
            biorxiv=BioRxivConfig(
                enabled="biorxiv" in sources,
                from_days_ago=config.candidates.window_days,
            ),
            medrxiv=MedRxivConfig(
                enabled="medrxiv" in sources,
                from_days_ago=config.candidates.window_days,
            ),
            altmetric=AltmetricConfig(enabled=False),
        ),
        scoring=ScoringConfig.model_validate(LEGACY_V1_SCORING_POLICY),
    )


def load_effective_runtime(workspace: Path | str) -> EffectiveRuntimeConfig:
    loaded = load_workspace_config(Path(workspace))
    if loaded.source is ConfigSource.LEGACY:
        assert loaded.legacy_settings is not None
        return EffectiveRuntimeConfig(
            source=loaded.source,
            config_schema_version=None,
            config_fingerprint_sha256=_fingerprint({"source": "legacy"}),
            settings=loaded.legacy_settings,
        )

    config = loaded.config
    assert config is not None
    safe_semantics = {
        "schema_version": config.schema_version,
        "zotero": {"library_type": config.zotero.library_type},
        "candidates": config.candidates.model_dump(mode="json"),
        "ranking": config.ranking.model_dump(mode="json"),
        "embedding": config.embedding.model_dump(mode="json"),
        "ai_features": {
            route.feature: {
                "provider": route.provider_id,
                "protocol": route.protocol_id,
                "runtime": route.adapter_status.value,
            }
            for route in loaded.routes
        },
        "outputs": config.outputs.model_dump(mode="json"),
    }
    return EffectiveRuntimeConfig(
        source=loaded.source,
        config_schema_version=config.schema_version,
        config_fingerprint_sha256=_fingerprint(safe_semantics),
        settings=_v2_settings(loaded),
        top_n=config.ranking.top_n,
        max_preprint_ratio=config.ranking.max_preprint_ratio,
        output_formats=tuple(config.outputs.formats),
        publish_requested=config.outputs.publish,
        journal_metrics="bundled",
        feature_routes=loaded.routes,
    )


__all__ = ["EffectiveRuntimeConfig", "load_effective_runtime"]
