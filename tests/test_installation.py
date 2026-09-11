"""Dedicated packaging CI supplies isolated, fully installed environments.

The lightweight E0 job does not install torch/build tools. It skips these cases;
the packaging job sets E1_INSTALLATION_REQUIRED=1 so missing setup is a failure.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

import pytest
import yaml

from .helpers import ROOT, FIXTURES, GOLDENS, assert_json, read_json


def configured(name):
    value = os.getenv(name)
    if not value:
        if os.getenv("E1_INSTALLATION_REQUIRED") == "1":
            pytest.fail(f"Packaging CI must configure {name}")
        pytest.skip("Dedicated packaging job supplies isolated installations")
    path = Path(value)
    assert path.exists(), path
    return path


def environment(workspace):
    env = dict(os.environ)
    for key in list(env):
        if key in {"PYTHONPATH", "PYTHONHOME", "E1_DOTENV", "E1_PARENT_DOTENV"} or any(
                word in key for word in ("ZOTERO", "SUPABASE", "MAILTO", "ALTMETRIC")):
            del env[key]
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HOME=str(workspace / "hf-cache"),
               PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", PYTHONHASHSEED="0", TZ="UTC")
    return env


def run(args, workspace, *, success=True):
    result = subprocess.run([str(a) for a in args], cwd=workspace, env=environment(workspace),
                            text=True, capture_output=True, timeout=120)
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_wheel_and_sdist_are_self_contained():
    dist = configured("E1_DIST_DIR")
    original = (ROOT / "data/journal_metrics.csv").read_bytes()
    with zipfile.ZipFile(next(dist.glob("*.whl"))) as wheel:
        names = wheel.namelist()
        assert wheel.read("zotwatch/resources/journal_metrics.csv") == original
        assert all(name.startswith(("src/", "zotwatch/", "zotwatch-2.0.0.dev1.dist-info/")) for name in names)
        assert not any(name.endswith((".env", ".sqlite", ".index")) for name in names)
    with tarfile.open(next(dist.glob("*.tar.gz"))) as archive:
        names = archive.getnames()
        resource = next(n for n in names if n.endswith("zotwatch/resources/journal_metrics.csv"))
        assert archive.extractfile(resource).read() == original
        forbidden_top_level = {"tests", "config", "data", "reports", ".github"}
        assert not any(
            len(Path(name).parts) > 1 and Path(name).parts[1] in forbidden_top_level
            for name in names
        )


@pytest.mark.parametrize("kind", ["WHEEL", "EDITABLE"])
def test_installed_commands_config_and_errors(kind, workspace):
    python = configured(f"E1_{kind}_PYTHON")
    command = python.parent / "zotwatch"
    assert "--workspace" in run([command, "--help"], workspace).stdout
    assert "--base-dir" in run([python, "-I", "-m", "src.cli", "--help"], workspace).stdout
    result = run([command, "profile", "--workspace", str(workspace)], workspace, success=False)
    assert result.returncode == 1
    assert "ZOTERO_API_KEY" in result.stderr  # Config loaded; no credentials/network used.
    result = run([command, "watch", "--workspace", str(workspace / "missing")], workspace, success=False)
    assert result.returncode == 1 and "Configuration file not found" in result.stderr
    run([python, "-m", "pip", "check"], workspace)


@pytest.mark.parametrize("kind", ["WHEEL", "EDITABLE"])
@pytest.mark.parametrize("entry", ["new", "legacy"])
def test_installed_pipeline_matches_e0_goldens(kind, entry, workspace):
    python = configured(f"E1_{kind}_PYTHON")
    shutil.copytree(FIXTURES, workspace / "fixtures")
    script = workspace / "probe.py"
    shutil.copyfile(ROOT / "tests/installed_probe.py", script)
    scoring = workspace / "config/scoring.yaml"
    values = yaml.safe_load(scoring.read_text())
    values.update(whitelist_authors=["ada example"], whitelist_venues=["EXAMPLE JOURNAL"])
    scoring.write_text(yaml.safe_dump(values))
    (workspace / ".env").write_text("ZOTERO_USER_ID=123456\nZOTERO_API_KEY=synthetic-key\nE1_DOTENV=workspace\n")
    (workspace.parent / ".env").write_text("E1_PARENT_DOTENV=must-not-load\n")
    # Compare engine-owned files before/after; fixtures alone live in the caller.
    location = run([python, "-I", "-c", "import src; print(src.__file__)"], workspace).stdout.strip()
    package = Path(location).parent
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob("*.py")}
    result = run([python, "-I", script, entry], workspace)
    metadata = json.loads(result.stdout.splitlines()[-1])
    if kind == "WHEEL":
        assert "site-packages" in metadata["cli_module"]
        assert not Path(metadata["cli_module"]).is_relative_to(ROOT)
    state, reports = Path(metadata["state"]), Path(metadata["reports"])
    assert not (workspace / "data").exists()
    assert not (workspace / "reports").exists()
    assert_json(read_json(Path(metadata["profile"])), read_json(GOLDENS / "profile.json"))
    assert Path(metadata["manifest"]).is_file()
    assert Path(metadata["profile"]).is_relative_to(state / "computational/generations")
    assert_json(read_json(reports / "ranking.json"), read_json(GOLDENS / "ranking.json"))
    assert (reports / "full.xml").read_bytes() == (GOLDENS / "ranked.xml").read_bytes()
    assert (reports / "full.html").read_bytes() == (GOLDENS / "ranked.html").read_bytes()
    assert (reports / "report-20260114.html").exists()
    assert Path(metadata["v2_feed"]).is_file()
    assert Path(metadata["v2_report"]).is_file()
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob("*.py")}
    assert not any(p.suffix in {".sqlite", ".index", ".csv"} for p in package.rglob("*"))
