from __future__ import annotations

from copy import deepcopy
import json

import pytest

from zotwatch.config import ConfigError
from zotwatch.config.loader import load_v2_config
from zotwatch.config.public_candidates import load_public_candidate_connection
from zotwatch.config.schema import build_config_schema, load_published_schema
from zotwatch.config.semantic import resolve_feature_routes
from zotwatch.providers import (
    AdapterStatus,
    StandardUIStatus,
    credential_for_custom_connection,
    custom_connection_ids,
    get_provider,
    get_protocol,
    preset_provider_ids,
    protocol_ids,
)

from .test_config_v2 import minimal_config, write_config


def service_config(*, provider="openrouter", feature="summary") -> dict:
    data = minimal_config()
    data["ai"]["services"] = {"selected-service": {"provider": provider, "model": "manual-model"}}
    data["ai"]["features"][feature] = {"enabled": True, "service": "selected-service"}
    return data


def custom_config(*, protocol="openai-compatible-generation", connection_id="custom-1",
                  base_url="https://gateway.example.invalid/v1", feature="summary") -> dict:
    data = minimal_config()
    data["ai"]["services"] = {
        "selected-service": {
            "provider": "custom",
            "protocol": protocol,
            "base_url": base_url,
            "model": "user-entered-model",
            "connection_id": connection_id,
        }
    }
    data["ai"]["features"][feature] = {"enabled": True, "service": "selected-service"}
    return data


def load(tmp_path, data):
    path = tmp_path / "zotwatch.yaml"
    write_config(path, data)
    return load_v2_config(path)


def test_registry_separates_registration_adapter_and_ui_states():
    assert preset_provider_ids() == (
        "voyage", "dashscope", "openrouter", "deepseek", "openai", "anthropic"
    )
    assert protocol_ids() == (
        "openai-compatible-generation", "anthropic-compatible-generation"
    )
    for identifier in (*preset_provider_ids(), *protocol_ids()):
        definition = get_provider(identifier) or get_protocol(identifier)
        assert definition.registered
        assert definition.adapter_status is AdapterStatus.UNIMPLEMENTED
        assert definition.standard_ui_status is StandardUIStatus.HIDDEN


def test_custom_connections_have_fixed_personal_runner_mapping():
    assert custom_connection_ids() == ("custom-1", "custom-2", "custom-3", "custom-4")
    for index, connection_id in enumerate(custom_connection_ids(), start=1):
        credential = credential_for_custom_connection(connection_id)
        assert credential.workflow_secret_name == f"ZOTWATCH_CUSTOM_{index}_CREDENTIAL"
        assert credential.structured
    assert credential_for_custom_connection("CUSTOM_SLOT") is None
    assert credential_for_custom_connection("ZOTWATCH_CUSTOM_1_CREDENTIAL") is None


def test_published_schema_is_generated_from_registry():
    generated = build_config_schema()
    published = load_published_schema()
    assert published == generated
    definitions = published["$defs"]
    assert definitions["PresetServiceConfig"]["properties"]["provider"]["enum"] == list(
        preset_provider_ids()
    )
    custom = definitions["CustomServiceConfig"]["properties"]
    assert custom["protocol"]["enum"] == list(protocol_ids())
    assert custom["connection_id"]["enum"] == list(custom_connection_ids())


@pytest.mark.parametrize("provider", ["voyage", "dashscope", "openrouter", "deepseek", "openai", "anthropic"])
def test_registered_presets_parse_with_manual_model(tmp_path, provider):
    feature = "rerank" if provider == "voyage" else "summary"
    config = load(tmp_path, service_config(provider=provider, feature=feature))
    route = resolve_feature_routes(config)[0]
    assert route.provider_id == provider
    assert not route.runtime_available
    assert route.standard_ui_status is StandardUIStatus.HIDDEN


@pytest.mark.parametrize(
    "protocol", ["openai-compatible-generation", "anthropic-compatible-generation"]
)
def test_custom_generation_protocols_parse_without_runtime_adapter(tmp_path, protocol):
    config = load(tmp_path, custom_config(protocol=protocol))
    route = resolve_feature_routes(config)[0]
    assert route.provider_id == "custom"
    assert route.protocol_id == protocol
    assert route.credential_internal_id == "custom:custom-1"
    assert not route.runtime_available


def test_generation_can_rerank_but_native_rerank_cannot_summarize(tmp_path):
    config = load(tmp_path, custom_config(feature="rerank"))
    assert resolve_feature_routes(config)[0].feature == "rerank"
    with pytest.raises(ConfigError) as raised:
        load(tmp_path, service_config(provider="voyage", feature="summary"))
    assert raised.value.code == "CAPABILITY_MISMATCH"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://gateway.example.invalid/v1",
        "https://user:pass@gateway.example.invalid/v1",
        "https://gateway.example.invalid/v1?key=value",
        "https://gateway.example.invalid/v1#fragment",
        "https://localhost/v1",
        "https://service.local/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/latest",
        "https://10.0.0.1/v1",
    ],
)
def test_custom_url_restrictions_are_offline_and_redacted(tmp_path, base_url):
    with pytest.raises(ConfigError) as raised:
        load(tmp_path, custom_config(base_url=base_url))
    assert raised.value.code in {"CONFIG_SCHEMA", "CUSTOM_URL_INVALID"}
    assert base_url not in str(raised.value)


def test_custom_connection_binding_conflict_is_rejected(tmp_path):
    data = custom_config()
    data["ai"]["services"]["other-service"] = {
        **deepcopy(data["ai"]["services"]["selected-service"]),
        "base_url": "https://other.example.invalid/v1",
    }
    with pytest.raises(ConfigError) as raised:
        load(tmp_path, data)
    assert raised.value.code == "CUSTOM_CONNECTION_CONFLICT"
    assert "example.invalid" not in str(raised.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_key", "secret-value"),
        ("api_key_env", "OPENROUTER_API_KEY"),
        ("secret_name", "MY_SECRET"),
        ("credential_slot", "slot-9"),
        ("headers", {"Authorization": "secret-value"}),
        ("request_template", "arbitrary"),
        ("plugin", "module.callable"),
    ],
)
def test_service_cannot_define_credentials_or_request_mechanics(tmp_path, field, value):
    data = service_config()
    data["ai"]["services"]["selected-service"][field] = value
    with pytest.raises(ConfigError) as raised:
        load(tmp_path, data)
    assert raised.value.code == "CONFIG_SCHEMA"
    assert "secret-value" not in str(raised.value)


def test_public_candidate_connection_is_engine_owned_and_redacted():
    connection = load_public_candidate_connection()
    assert connection.provider == "public-api-v1"
    assert connection.base_url.startswith("https://")
    assert connection.publishable_key.startswith("sb_publishable_")
    rendered = repr(connection)
    assert "supabase" not in rendered
    assert "sb_publishable" not in rendered


def test_schema_artifact_is_stable_json():
    rendered = json.dumps(build_config_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    from importlib.resources import files

    assert files("zotwatch.resources").joinpath("config-v2.schema.json").read_text() == rendered
