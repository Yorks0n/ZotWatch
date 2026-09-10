from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from zotwatch.config import ConfigError, load_v2_config
from zotwatch.config.schema import build_config_schema


def minimal_config() -> dict:
    return {
        "schema_version": 2,
        "zotero": {"library_type": "user"},
        "candidates": {
            "provider": "public-api-v1",
            "sources": ["crossref", "arxiv", "biorxiv"],
            "window_days": 7,
        },
        "ranking": {"policy": "legacy-v1", "top_n": 20, "max_preprint_ratio": 0.3},
        "embedding": {
            "provider": "local",
            "model": "sentence-transformers/all-MiniLM-L6-v2",
        },
        "ai": {
            "services": {},
            "features": {"rerank": {"enabled": False}, "summary": {"enabled": False}},
        },
        "outputs": {"formats": ["rss", "html", "json"], "publish": False},
    }


def write_config(path, data) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_minimal_config_is_strict_and_ai_optional(tmp_path):
    path = tmp_path / "zotwatch.yaml"
    write_config(path, minimal_config())
    config = load_v2_config(path)
    assert config.schema_version == 2
    assert config.ai.services == {}
    assert not config.ai.features.rerank.enabled
    assert not config.ai.features.summary.enabled
    assert "api_key" not in str(config.model_dump())


@pytest.mark.parametrize(
    "mutate,pointer",
    [
        (lambda value: value.update(schema_version="2"), "/schema_version"),
        (lambda value: value["ranking"].update(top_n=0), "/ranking/top_n"),
        (lambda value: value["candidates"].update(window_days=30), "/candidates/window_days"),
        (lambda value: value["outputs"].update(formats=["rss", "rss"]), "/outputs/formats"),
        (lambda value: value.update(api_key="forbidden"), "/"),
    ],
)
def test_invalid_structure_is_rejected_without_echoing_values(tmp_path, mutate, pointer):
    data = deepcopy(minimal_config())
    mutate(data)
    path = tmp_path / "zotwatch.yaml"
    write_config(path, data)
    with pytest.raises(ConfigError) as raised:
        load_v2_config(path)
    assert raised.value.code == "CONFIG_SCHEMA"
    assert raised.value.json_pointer == pointer
    assert "forbidden" not in str(raised.value)


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    path = tmp_path / "zotwatch.yaml"
    path.write_text("schema_version: 2\nschema_version: 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="CONFIG_SYNTAX"):
        load_v2_config(path)


def test_schema_is_draft_2020_12_and_forbids_unknown_top_level_fields():
    schema = build_config_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
