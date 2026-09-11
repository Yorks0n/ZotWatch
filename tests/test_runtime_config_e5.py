from copy import deepcopy

import yaml

from zotwatch.config import ConfigSource
from zotwatch.runtime.config import load_effective_runtime

from .test_config_v2 import minimal_config


def write_v2(workspace, *, formats=("rss", "html")):
    data = deepcopy(minimal_config())
    data["outputs"]["formats"] = list(formats)
    (workspace / "zotwatch.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    return data


def test_basic_v2_maps_to_existing_runtime_without_user_public_key(
    workspace, monkeypatch
):
    write_v2(workspace)
    monkeypatch.setenv("ZOTERO_USER_ID", "12345")
    monkeypatch.setenv("ZOTERO_API_KEY", "synthetic-zotero-key")

    effective = load_effective_runtime(workspace)

    assert effective.source is ConfigSource.V2
    assert effective.config_schema_version == 2
    assert effective.top_n == 20
    assert effective.max_preprint_ratio == 0.3
    assert effective.output_formats == ("rss", "html")
    assert effective.journal_metrics == "bundled"
    assert effective.settings.zotero.api.user_id == "12345"
    assert effective.settings.sources.window_days == 7
    assert effective.settings.sources.public_api.enabled
    assert effective.settings.sources.public_api.publishable_key
    assert effective.settings.scoring.weights.model_dump() == {
        "similarity": 0.45,
        "recency": 0.15,
        "citations": 0.15,
        "altmetric": 0.10,
        "journal_quality": 0.04,
        "author_bonus": 0.02,
        "venue_bonus": 0.04,
    }
    rendered = repr(effective)
    assert "synthetic-zotero-key" not in rendered
    assert "sb_publishable" not in rendered


def test_legacy_runtime_uses_original_settings_not_projection(workspace, settings):
    effective = load_effective_runtime(workspace)
    assert effective.source is ConfigSource.LEGACY
    assert effective.settings.model_dump() == settings.model_dump()
    assert effective.config_schema_version is None

