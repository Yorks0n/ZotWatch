import re
import json
from pathlib import Path

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
