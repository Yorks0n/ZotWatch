from dataclasses import replace
from pathlib import Path
import shutil

from zotwatch.workflow.artifacts import (
    CHECKPOINT_ARTIFACT_NAME,
    ArtifactDescriptor,
    PriorRunContext,
    RestoreOutcome,
    WorkflowRun,
    restore_prior_checkpoint,
)
from zotwatch.workflow.checkpoint import CheckpointExpectation


class FakeSource:
    def __init__(self, runs, artifacts, bundles):
        self.runs = runs
        self.artifacts = artifacts
        self.bundles = bundles
        self.downloaded = []

    def list_completed_runs(self, context, limit):
        assert limit == 15
        return self.runs[:limit]

    def list_run_artifacts(self, context, run_id):
        return self.artifacts.get(run_id, [])

    def download_artifact(self, context, artifact_id, destination):
        self.downloaded.append(artifact_id)
        shutil.copytree(self.bundles[artifact_id], destination)
        return destination


def _run(run_id, *, event="workflow_dispatch", ref="refs/heads/main"):
    return WorkflowRun(
        run_id=run_id,
        repository_id=1234,
        workflow_id=77,
        workflow_path=".github/workflows/watch.yml",
        event=event,
        status="completed",
        head_ref=ref,
    )


def _context():
    return PriorRunContext(
        repository="example/private-zotwatch",
        repository_id=1234,
        workflow_id=77,
        workflow_path=".github/workflows/watch.yml",
        current_run_id=500,
        ref="refs/heads/main",
        event="workflow_dispatch",
    )


def _expectation(run_id):
    context = _context()
    return CheckpointExpectation(
        engine_repository="Yorks0n/ZotWatch",
        workspace_repository=context.repository,
        workspace_repository_id=context.repository_id,
        caller_workflow_id=context.workflow_id,
        caller_workflow_path=context.workflow_path,
        caller_event="workflow_dispatch",
        caller_run_id=run_id,
        caller_ref=context.ref,
        config_fingerprint_sha256="b" * 64,
    )


def test_bounded_search_skips_validate_only_and_invalid_newer_checkpoint(tmp_path, monkeypatch):
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    source = FakeSource(
        [_run(500), _run(499), _run(498), _run(497)],
        {
            499: [],  # validate-only success
            498: [ArtifactDescriptor(91, CHECKPOINT_ARTIFACT_NAME, False, 498, None)],
            497: [ArtifactDescriptor(90, CHECKPOINT_ARTIFACT_NAME, False, 497, None)],
        },
        {91: bad, 90: good},
    )

    def fake_import(bundle, state_dir, expectation):
        if bundle.name.startswith("91-"):
            raise ValueError("bad checkpoint")
        Path(state_dir).mkdir(parents=True)
        return object()

    monkeypatch.setattr("zotwatch.workflow.artifacts.import_checkpoint", fake_import)
    outcome = restore_prior_checkpoint(
        source, _context(), tmp_path / "state", tmp_path / "staging",
        expectation_factory=_expectation,
    )

    assert outcome == RestoreOutcome(restored=True, source_run_id=497, artifact_id=90)
    assert source.downloaded == [91, 90]


def test_search_is_bounded_and_does_not_require_overall_success(tmp_path, monkeypatch):
    runs = [_run(i) for i in range(499, 470, -1)]
    source = FakeSource(runs, {}, {})
    monkeypatch.setattr(
        "zotwatch.workflow.artifacts.import_checkpoint",
        lambda *args: (_ for _ in ()).throw(AssertionError("not reached")),
    )
    outcome = restore_prior_checkpoint(
        source, _context(), tmp_path / "state", tmp_path / "staging",
        expectation_factory=_expectation,
    )
    assert outcome == RestoreOutcome(restored=False, source_run_id=None, artifact_id=None)
