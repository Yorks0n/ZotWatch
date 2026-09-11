from copy import deepcopy
import shutil

import yaml

from src import cli as legacy_cli
from zotwatch import cli

from .test_config_v2 import minimal_config


def write_config(workspace, formats=("rss", "html"), *, ai=None):
    if (workspace / "config").exists():
        shutil.rmtree(workspace / "config")
    data = deepcopy(minimal_config())
    data["outputs"]["formats"] = list(formats)
    if ai is not None:
        data["ai"] = ai
    (workspace / "zotwatch.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def credentials(monkeypatch):
    monkeypatch.setenv("ZOTERO_USER_ID", "12345")
    monkeypatch.setenv("ZOTERO_API_KEY", "synthetic-zotero-key")


def test_v2_watch_dispatches_existing_pipeline_with_config_values(
    workspace, monkeypatch
):
    write_config(workspace)
    credentials(monkeypatch)
    calls = []
    monkeypatch.setattr(
        legacy_cli,
        "run_watch",
        lambda base, settings, storage, **kwargs: calls.append((base, settings, kwargs)),
    )
    assert cli.main(["watch", "--workspace", str(workspace)]) == 0
    assert len(calls) == 1
    base, settings, kwargs = calls[0]
    assert base == workspace
    assert settings.sources.public_api.enabled
    assert kwargs["rss"] is True
    assert kwargs["report"] is True
    assert kwargs["top"] == 20
    assert kwargs["push"] is False
    assert kwargs["journal_metrics"] == "bundled"


def test_json_and_ai_fail_before_pipeline_side_effects(workspace, monkeypatch, capsys):
    credentials(monkeypatch)
    called = []
    monkeypatch.setattr(legacy_cli, "run_watch", lambda *args, **kwargs: called.append(1))

    write_config(workspace, formats=("json",))
    assert cli.main(["watch", "--workspace", str(workspace)]) == 3
    assert "OUTPUT_FORMAT_UNAVAILABLE" in capsys.readouterr().err
    assert called == []

    ai = {
        "services": {"s": {"provider": "openrouter", "model": "some-model"}},
        "features": {
            "rerank": {"enabled": False},
            "summary": {"enabled": True, "service": "s"},
        },
    }
    write_config(workspace, formats=("rss",), ai=ai)
    assert cli.main(["watch", "--workspace", str(workspace)]) == 3
    assert "CAPABILITY_UNAVAILABLE" in capsys.readouterr().err
    assert called == []


def test_v2_rejects_legacy_run_overlay_before_pipeline(workspace, monkeypatch, capsys):
    write_config(workspace, formats=("rss",))
    credentials(monkeypatch)
    called = []
    monkeypatch.setattr(legacy_cli, "run_watch", lambda *args, **kwargs: called.append(1))
    assert cli.main(["watch", "--workspace", str(workspace), "--top", "5"]) == 2
    assert "CONFIG_OPTION_UNSUPPORTED" in capsys.readouterr().err
    assert called == []
