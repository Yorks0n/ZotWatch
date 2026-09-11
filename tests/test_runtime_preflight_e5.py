from copy import deepcopy

import yaml

from zotwatch.runtime.config import load_effective_runtime
from zotwatch.runtime.preflight import preflight

from .test_config_v2 import minimal_config


def write_config(workspace, data):
    (workspace / "zotwatch.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def test_basic_rss_html_needs_no_ai_or_public_pool_credential(workspace, monkeypatch):
    data = minimal_config()
    data["outputs"]["formats"] = ["rss", "html"]
    write_config(workspace, data)
    monkeypatch.setenv("ZOTERO_USER_ID", "12345")
    monkeypatch.setenv("ZOTERO_API_KEY", "synthetic-zotero-key")

    report = preflight(load_effective_runtime(workspace))

    assert report.ready
    assert report.error_code is None
    assert {item.requirement_id for item in report.requirements} == {
        "zotero.identity",
        "zotero.read",
        "public-candidates",
    }
    public = next(
        item for item in report.requirements if item.requirement_id == "public-candidates"
    )
    assert public.user_credential_required is False
    assert "API_KEY" not in report.model_dump_json()
    assert "synthetic-zotero-key" not in report.model_dump_json()


def test_e5a_json_is_explicitly_unavailable(workspace, monkeypatch):
    write_config(workspace, minimal_config())
    monkeypatch.setenv("ZOTERO_USER_ID", "12345")
    monkeypatch.setenv("ZOTERO_API_KEY", "synthetic-zotero-key")
    report = preflight(load_effective_runtime(workspace))
    assert not report.ready
    assert report.error_code == "OUTPUT_FORMAT_UNAVAILABLE"


def test_registered_but_unimplemented_ai_is_not_executable(workspace, monkeypatch):
    data = deepcopy(minimal_config())
    data["outputs"]["formats"] = ["rss"]
    data["ai"] = {
        "services": {
            "summary-service": {"provider": "openrouter", "model": "some-model"}
        },
        "features": {
            "rerank": {"enabled": False},
            "summary": {"enabled": True, "service": "summary-service"},
        },
    }
    write_config(workspace, data)
    monkeypatch.setenv("ZOTERO_USER_ID", "12345")
    monkeypatch.setenv("ZOTERO_API_KEY", "synthetic-zotero-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-validated-or-printed")
    report = preflight(load_effective_runtime(workspace))
    assert not report.ready
    assert report.error_code == "CAPABILITY_UNAVAILABLE"
    assert "must-not-be-validated-or-printed" not in report.model_dump_json()
    assert "OPENROUTER_API_KEY" not in report.model_dump_json()


def test_missing_zotero_credentials_are_presence_not_verification(workspace, monkeypatch):
    data = minimal_config()
    data["outputs"]["formats"] = ["html"]
    write_config(workspace, data)
    monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
    monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
    report = preflight(load_effective_runtime(workspace))
    assert not report.ready
    assert report.error_code == "CREDENTIAL_MISSING"
    zotero = [item for item in report.requirements if item.requirement_id.startswith("zotero")]
    assert all(not item.configured for item in zotero)
    assert all(item.verification == "not_requested" for item in zotero)

