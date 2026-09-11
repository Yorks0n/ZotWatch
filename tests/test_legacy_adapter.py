from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from src.settings import load_settings
from zotwatch import cli as public_cli
from zotwatch.config import ConfigError, ConfigSource, load_workspace_config
from zotwatch.config.legacy import LegacyConfigAdapter, project_legacy_to_v2

from .test_config_v2 import minimal_config, write_config


def file_state(workspace: Path) -> dict[str, tuple[int, str]]:
    return {
        str(path.relative_to(workspace)): (
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted((workspace / "config").glob("*.yaml"))
    }


def test_legacy_adapter_returns_exact_original_settings_without_writes(workspace):
    before = file_state(workspace)
    direct = load_settings(workspace)
    loaded = LegacyConfigAdapter().load(workspace)
    assert loaded.settings.model_dump(by_alias=True) == direct.model_dump(by_alias=True)
    assert loaded.report.config is not None
    assert not loaded.report.has_blocking_loss
    assert loaded.report.config.ai.services == {}
    assert file_state(workspace) == before
    assert not (workspace / "zotwatch.yaml").exists()


def test_nonstandard_legacy_config_keeps_runtime_behavior_and_reports_loss(workspace):
    scoring_path = workspace / "config/scoring.yaml"
    scoring = yaml.safe_load(scoring_path.read_text())
    scoring["weights"]["similarity"] = 0.5
    scoring_path.write_text(yaml.safe_dump(scoring), encoding="utf-8")
    settings = load_settings(workspace)
    assert settings.scoring.weights.similarity == 0.5
    report = project_legacy_to_v2(settings)
    assert report.config is None
    assert report.has_blocking_loss
    assert any(issue.path == "/scoring" for issue in report.issues)


def test_v2_and_any_legacy_file_never_merge(workspace):
    write_config(workspace / "zotwatch.yaml", minimal_config())
    with pytest.raises(ConfigError) as raised:
        load_workspace_config(workspace)
    assert raised.value.code == "CONFIG_MIXED_MODES"


def test_v2_only_workspace_loads_internal_public_connection(tmp_path):
    write_config(tmp_path / "zotwatch.yaml", minimal_config())
    loaded = load_workspace_config(tmp_path)
    assert loaded.source is ConfigSource.V2
    assert loaded.legacy_settings is None
    assert loaded.public_candidates is not None
    assert "publishable" not in repr(loaded)


def test_validation_cli_is_offline_and_does_not_claim_ai_runtime(tmp_path, capsys):
    data = minimal_config()
    data["ai"]["services"] = {"summary-service": {"provider": "openrouter", "model": "manual"}}
    data["ai"]["features"]["summary"] = {"enabled": True, "service": "summary-service"}
    write_config(tmp_path / "zotwatch.yaml", data)
    assert public_cli.main(["config", "validate", "--workspace", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert "schema and semantics only" in output.out
    assert "0/1" in output.out
    assert "openrouter" not in output.out


def test_public_cli_gates_e5a_json_and_rejects_mixed_modes(tmp_path, workspace, capsys):
    v2_workspace = tmp_path / "v2-only"
    v2_workspace.mkdir()
    write_config(v2_workspace / "zotwatch.yaml", minimal_config())
    assert public_cli.main(["watch", "--workspace", str(v2_workspace)]) == 3
    assert "OUTPUT_FORMAT_UNAVAILABLE" in capsys.readouterr().err

    write_config(workspace / "zotwatch.yaml", minimal_config())
    assert public_cli.main(["profile", "--workspace", str(workspace)]) == 2
    assert "CONFIG_MIXED_MODES" in capsys.readouterr().err
