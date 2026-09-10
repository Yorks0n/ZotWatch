from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.settings import Settings, load_settings

from .models import ZotWatchConfigV2
from .semantic import resolve_feature_routes


_LEGACY_SCORING_POLICY = {
    "weights": {
        "similarity": 0.45,
        "recency": 0.15,
        "citations": 0.15,
        "altmetric": 0.10,
        "journal_quality": 0.04,
        "author_bonus": 0.02,
        "venue_bonus": 0.04,
    },
    "thresholds": {"must_read": 0.75, "consider": 0.5},
    "decay_days": {"fast": 3, "medium": 7, "slow": 30},
    "whitelist_authors": [],
    "whitelist_venues": [],
}
_SOURCE_ORDER = ("crossref", "arxiv", "biorxiv", "medrxiv", "openalex")


@dataclass(frozen=True)
class LegacyMappingIssue:
    path: str
    code: str
    message: str
    blocking: bool


@dataclass(frozen=True)
class LegacyMappingReport:
    config: ZotWatchConfigV2 | None = field(repr=False)
    mapped_fields: tuple[str, ...]
    compatibility_fields: tuple[str, ...]
    issues: tuple[LegacyMappingIssue, ...]

    @property
    def has_blocking_loss(self) -> bool:
        return any(issue.blocking for issue in self.issues)


@dataclass(frozen=True)
class LegacyLoadResult:
    settings: Settings = field(repr=False)
    report: LegacyMappingReport


def _issue(path: str, code: str, message: str, *, blocking: bool = True) -> LegacyMappingIssue:
    return LegacyMappingIssue(path=path, code=code, message=message, blocking=blocking)


def project_legacy_to_v2(settings: Settings, *, top_n: int = 20) -> LegacyMappingReport:
    issues: list[LegacyMappingIssue] = []
    compatibility = (
        "/zotero/api/user_id",
        "/zotero/api/api_key_env",
        "/sources/public_api/base_url",
        "/sources/public_api/publishable_key",
        "/sources/public_api/api_key_env",
        "/sources/openalex/mailto",
        "/sources/crossref/mailto",
        "/sources/arxiv/categories",
        "/sources/biorxiv/from_days_ago",
        "/sources/medrxiv/from_days_ago",
        "/sources/altmetric",
    )
    if settings.zotero.mode != "api":
        issues.append(_issue("/zotero/mode", "LEGACY_MAPPING_LOSS", "BBT mode has no v2 representation"))
    if not settings.sources.public_api.enabled:
        issues.append(
            _issue(
                "/sources/public_api/enabled",
                "LEGACY_MAPPING_LOSS",
                "direct-fetch mode is an advanced legacy-only path",
            )
        )
    if settings.sources.window_days != 7:
        issues.append(
            _issue("/sources/window_days", "LEGACY_MAPPING_LOSS", "v2 legacy policy fixes window_days to 7")
        )
    if settings.sources.altmetric.enabled:
        issues.append(
            _issue("/sources/altmetric/enabled", "LEGACY_MAPPING_LOSS", "Altmetric is legacy-only")
        )
    if settings.scoring.model_dump() != _LEGACY_SCORING_POLICY:
        issues.append(
            _issue("/scoring", "LEGACY_MAPPING_LOSS", "custom legacy scoring cannot be projected losslessly")
        )

    sources = [name for name in _SOURCE_ORDER if getattr(settings.sources, name).enabled]
    if not sources:
        issues.append(_issue("/sources", "LEGACY_MAPPING_LOSS", "v2 candidates require at least one source"))
    if not 1 <= top_n <= 200:
        issues.append(_issue("/ranking/top_n", "LEGACY_MAPPING_LOSS", "projection top_n is outside v2 bounds"))

    config = None
    if not any(issue.blocking for issue in issues):
        config = ZotWatchConfigV2.model_validate(
            {
                "schema_version": 2,
                "zotero": {"library_type": "user"},
                "candidates": {
                    "provider": "public-api-v1",
                    "sources": sources,
                    "window_days": 7,
                },
                "ranking": {
                    "policy": "legacy-v1",
                    "top_n": top_n,
                    "max_preprint_ratio": 0.3,
                },
                "embedding": {
                    "provider": "local",
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                },
                "ai": {
                    "services": {},
                    "features": {
                        "rerank": {"enabled": False},
                        "summary": {"enabled": False},
                    },
                },
                "outputs": {"formats": ["rss", "html", "json"], "publish": False},
            }
        )
        resolve_feature_routes(config)

    return LegacyMappingReport(
        config=config,
        mapped_fields=(
            "/zotero/library_type",
            "/candidates/provider",
            "/candidates/sources",
            "/candidates/window_days",
            "/ranking/policy",
            "/ranking/top_n",
            "/ranking/max_preprint_ratio",
            "/embedding",
            "/ai",
            "/outputs",
        ),
        compatibility_fields=compatibility,
        issues=tuple(issues),
    )


class LegacyConfigAdapter:
    def load(self, workspace: Path | str) -> LegacyLoadResult:
        settings = load_settings(Path(workspace))
        return LegacyLoadResult(settings=settings, report=project_legacy_to_v2(settings))


__all__ = [
    "LegacyConfigAdapter",
    "LegacyLoadResult",
    "LegacyMappingIssue",
    "LegacyMappingReport",
    "project_legacy_to_v2",
]
