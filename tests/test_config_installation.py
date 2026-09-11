"""Installed E2 contract checks run only in the dedicated packaging job."""

from __future__ import annotations

from pathlib import Path
import shutil
import tarfile
import zipfile

import pytest

from .helpers import ROOT
from .test_installation import configured, run


CONFIG_FIXTURES = ROOT / "tests/fixtures/config-v2"


def _v2_workspace(tmp_path: Path, fixture: str) -> Path:
    workspace = tmp_path / fixture.removesuffix(".yaml")
    workspace.mkdir()
    shutil.copyfile(CONFIG_FIXTURES / fixture, workspace / "zotwatch.yaml")
    return workspace


def test_wheel_and_sdist_include_e2_engine_resources():
    dist = configured("E1_DIST_DIR")
    expected_schema = (ROOT / "zotwatch/resources/config-v2.schema.json").read_bytes()
    expected_public = (ROOT / "zotwatch/resources/public-candidates-v1.json").read_bytes()
    with zipfile.ZipFile(next(dist.glob("*.whl"))) as wheel:
        assert wheel.read("zotwatch/resources/config-v2.schema.json") == expected_schema
        assert wheel.read("zotwatch/resources/public-candidates-v1.json") == expected_public
        names = set(wheel.namelist())
        assert "zotwatch/config/loader.py" in names
        assert "zotwatch/providers/registry.py" in names
        assert "zotwatch/runtime/config.py" in names
        assert "zotwatch/runtime/preflight.py" in names
    with tarfile.open(next(dist.glob("*.tar.gz"))) as archive:
        names = archive.getnames()
        assert any(name.endswith("zotwatch/resources/config-v2.schema.json") for name in names)
        assert any(name.endswith("zotwatch/resources/public-candidates-v1.json") for name in names)


@pytest.mark.parametrize("kind", ["WHEEL", "EDITABLE"])
@pytest.mark.parametrize("fixture", ["minimal.yaml", "preset.yaml", "custom.yaml"])
def test_installed_validate_works_outside_checkout(kind, fixture, tmp_path):
    python = configured(f"E1_{kind}_PYTHON")
    workspace = _v2_workspace(tmp_path, fixture)
    result = run([python.parent / "zotwatch", "config", "validate", "--workspace", workspace], workspace)
    assert "schema and semantics only" in result.stdout
    assert "gateway.example" not in result.stdout + result.stderr
    assert "API_KEY" not in result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["WHEEL", "EDITABLE"])
def test_installed_rejects_secret_fields_and_unavailable_json(kind, tmp_path):
    python = configured(f"E1_{kind}_PYTHON")
    invalid = _v2_workspace(tmp_path, "invalid-secret.yaml")
    result = run(
        [python.parent / "zotwatch", "config", "validate", "--workspace", invalid],
        invalid,
        success=False,
    )
    assert result.returncode == 2
    assert "CONFIG_SCHEMA" in result.stderr
    assert "OPENROUTER_API_KEY" not in result.stdout + result.stderr

    valid = _v2_workspace(tmp_path, "minimal.yaml")
    result = run(
        [python.parent / "zotwatch", "watch", "--workspace", valid], valid, success=False
    )
    assert result.returncode == 3
    assert "OUTPUT_FORMAT_UNAVAILABLE" in result.stderr
