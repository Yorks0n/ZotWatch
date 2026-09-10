"""ZotWatch v2 configuration models and loaders."""

from .errors import ConfigError
from .loader import load_v2_config, load_v2_workspace
from .models import ZotWatchConfigV2
from .public_candidates import PublicCandidateConnection, load_public_candidate_connection
from .semantic import ResolvedFeatureRoute, resolve_feature_routes
from .workspace import ConfigSource, LoadedWorkspaceConfig, detect_config_source, load_workspace_config

__all__ = [
    "ConfigError",
    "ConfigSource",
    "LoadedWorkspaceConfig",
    "PublicCandidateConnection",
    "ResolvedFeatureRoute",
    "ZotWatchConfigV2",
    "load_public_candidate_connection",
    "load_v2_config",
    "load_v2_workspace",
    "load_workspace_config",
    "detect_config_source",
    "resolve_feature_routes",
]
