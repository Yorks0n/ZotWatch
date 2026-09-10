from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from src.settings import Settings

from .errors import ConfigError
from .legacy import LegacyConfigAdapter, LegacyMappingReport
from .loader import load_v2_workspace
from .models import ZotWatchConfigV2
from .public_candidates import PublicCandidateConnection, load_public_candidate_connection
from .semantic import ResolvedFeatureRoute, resolve_feature_routes


_LEGACY_FILES = (
    Path("config/zotero.yaml"),
    Path("config/sources.yaml"),
    Path("config/scoring.yaml"),
)


class ConfigSource(str, Enum):
    V2 = "v2"
    LEGACY = "legacy"


@dataclass(frozen=True)
class LoadedWorkspaceConfig:
    source: ConfigSource
    config: ZotWatchConfigV2 | None = field(repr=False)
    routes: tuple[ResolvedFeatureRoute, ...]
    legacy_settings: Settings | None = field(default=None, repr=False)
    legacy_report: LegacyMappingReport | None = None
    public_candidates: PublicCandidateConnection | None = field(default=None, repr=False)


def detect_config_source(workspace: Path | str) -> ConfigSource:
    root = Path(workspace)
    has_v2 = (root / "zotwatch.yaml").is_file()
    legacy_present = tuple(path for path in _LEGACY_FILES if (root / path).is_file())
    if has_v2 and legacy_present:
        raise ConfigError(
            "CONFIG_MIXED_MODES",
            "zotwatch.yaml cannot coexist with legacy config files",
        )
    return ConfigSource.V2 if has_v2 else ConfigSource.LEGACY


def load_workspace_config(workspace: Path | str) -> LoadedWorkspaceConfig:
    root = Path(workspace)
    source = detect_config_source(root)
    if source is ConfigSource.V2:
        config = load_v2_workspace(root)
        return LoadedWorkspaceConfig(
            source=source,
            config=config,
            routes=resolve_feature_routes(config),
            public_candidates=load_public_candidate_connection(),
        )
    legacy = LegacyConfigAdapter().load(root)
    return LoadedWorkspaceConfig(
        source=source,
        config=legacy.report.config,
        routes=(),
        legacy_settings=legacy.settings,
        legacy_report=legacy.report,
    )


__all__ = ["ConfigSource", "LoadedWorkspaceConfig", "detect_config_source", "load_workspace_config"]
