import re
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

from zotwatch.providers.registry import custom_connection_ids, get_custom_credential, get_provider, preset_provider_ids


ROOT = Path(__file__).resolve().parents[1]
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def _uses(document):
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                yield step["uses"]


def test_compute_workflow_has_static_secrets_and_no_publish_permissions():
    document = _workflow("run.yml")
    call = document[True]["workflow_call"]
    declared = set(call["secrets"])
    expected = {"ZOTERO_USER_ID", "ZOTERO_API_KEY"}
    expected.update(get_provider(provider).credential.workflow_secret_name for provider in preset_provider_ids())
    expected.update(get_custom_credential(connection).workflow_secret_name for connection in custom_connection_ids())
    assert declared == expected
    serialized = (ROOT / ".github/workflows/run.yml").read_text()
    assert "pages: write" not in serialized
    assert "id-token: write" not in serialized
    assert "secrets: inherit" not in serialized
    assert "pull_request:" not in serialized


def test_pages_permissions_are_in_separate_workflow():
    document = _workflow("publish-pages.yml")
    permissions = next(iter(document["jobs"].values()))["permissions"]
    assert permissions == {
        "contents": "read", "actions": "read", "pages": "write", "id-token": "write"
    }
    assert "secrets" not in document[True]["workflow_call"]


def test_every_external_action_is_pinned_to_full_sha():
    reviewed = json.loads((ROOT / "tests/fixtures/p2/action-pins.json").read_text())
    observed = {}
    for name in ("run.yml", "publish-pages.yml"):
        for use in _uses(_workflow(name)):
            owner_repo, revision = use.rsplit("@", 1)
            assert owner_repo.startswith("actions/")
            assert FULL_SHA.fullmatch(revision)
            observed[owner_repo] = revision
    assert observed == {name: item["sha"] for name, item in reviewed.items()}


def test_compute_checkout_contract_uses_job_workflow_identity():
    text = (ROOT / ".github/workflows/run.yml").read_text()
    assert "repository: ${{ job.workflow_repository }}" in text
    assert "ref: ${{ job.workflow_sha }}" in text
    assert "path: engine" in text
    assert "persist-credentials: false" in text
    assert "actions/cache" not in text
    assert "profile.sqlite" not in text
    assert "secrets: inherit" not in text


def test_config_inputs_reach_inspection_as_literal_arguments(tmp_path):
    steps = _workflow("run.yml")["jobs"]["compute"]["steps"]
    inspect = next(step for step in steps if step.get("id") == "config")
    assert inspect["env"] == {
        "INPUT_CONFIG_PATH": "${{ inputs['config-path'] }}",
        "INPUT_EXPECTED_CONFIG_SHA": "${{ inputs['expected-config-sha'] }}",
    }
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['CAPTURE_ARGS'], 'w') as output:\n"
        "    json.dump(sys.argv[1:], output)\n"
    )
    fake_python.chmod(0o755)
    config_path = "zotwatch.yaml'; touch config-injected; echo '"
    expected_sha = "abc'; touch sha-injected; echo '"
    capture = tmp_path / "args.json"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_WORKSPACE": str(tmp_path),
        "INPUT_CONFIG_PATH": config_path,
        "INPUT_EXPECTED_CONFIG_SHA": expected_sha,
        "CAPTURE_ARGS": str(capture),
    }
    script = inspect["run"].replace("${{ inputs.mode }}", "run").replace(
        "${{ inputs['full-rebuild'] }}", "false"
    )
    subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, env=env, check=True)
    assert json.loads(capture.read_text()) == [
        "-m", "zotwatch.workflow", "inspect-config", "--workspace",
        str(tmp_path / "workspace"), "--config-path", config_path,
        "--expected-config-sha", expected_sha, "--mode", "run",
        "--full-rebuild", "false", "--github-output",
    ]
    assert not (tmp_path / "config-injected").exists()
    assert not (tmp_path / "sha-injected").exists()


def test_checkpoint_search_and_upload_contract_are_fixed():
    text = (ROOT / ".github/workflows/run.yml").read_text()
    assert "zotwatch-state-checkpoint-v1" in text
    assert "retention-days: 30" in text
    assert "actions: read" in text
    assert "pull_request" not in text
    assert "job.workflow_repository" in text
    assert "job.workflow_sha" in text


def test_runtime_and_model_versions_are_immutable():
    constraints = (ROOT / "constraints/p2-runtime-linux-py311.txt").read_text().splitlines()
    pins = [line for line in constraints if line and not line.startswith("#")]
    assert pins
    assert all("==" in line and not any(token in line for token in (">=", "~=", "*")) for line in pins)
    from src.vectorizer import DEFAULT_MODEL_REVISION
    assert FULL_SHA.fullmatch(DEFAULT_MODEL_REVISION)
