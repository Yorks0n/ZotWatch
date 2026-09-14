import json
from pathlib import Path

import pytest

from src.build_profile import ProfileBuilder
from zotwatch.workflow.checkpoint import (
    CheckpointContext,
    CheckpointError,
    CheckpointExpectation,
    export_checkpoint,
    import_checkpoint,
)
from .helpers import FixedVectors


def _context(*, run_id="401", ref="refs/heads/main"):
    return CheckpointContext(
        source_result_status="succeeded",
        source_engine_run_id="engine-run-1",
        engine_repository="Yorks0n/ZotWatch",
        engine_sha="a" * 40,
        workspace_repository="example/private-zotwatch",
        workspace_repository_id=1234,
        caller_workflow_id=77,
        caller_workflow_path=".github/workflows/watch.yml",
        caller_event="workflow_dispatch",
        caller_run_id=int(run_id),
        caller_run_attempt=1,
        caller_ref=ref,
        config_fingerprint_sha256="b" * 64,
    )


def _expectation(context):
    return CheckpointExpectation(
        engine_repository=context.engine_repository,
        workspace_repository=context.workspace_repository,
        workspace_repository_id=context.workspace_repository_id,
        caller_workflow_id=context.caller_workflow_id,
        caller_workflow_path=context.caller_workflow_path,
        caller_event=context.caller_event,
        caller_run_id=context.caller_run_id,
        caller_ref=context.caller_ref,
        config_fingerprint_sha256=context.config_fingerprint_sha256,
    )


def test_checkpoint_round_trip_is_bounded_to_current_generation(
    workspace, library, settings, tmp_path
):
    ProfileBuilder(workspace, library, settings, FixedVectors()).run()
    state = workspace / "data"
    (state / "runs").mkdir()
    (state / "runs/history.json").write_text("{}")
    older = state / "computational/generations/older"
    older.mkdir()
    (older / "private").write_text("old")
    bundle = tmp_path / "checkpoint"
    context = _context()

    exported = export_checkpoint(state, bundle, context)

    assert exported == bundle
    assert (bundle / "checkpoint.json").is_file()
    assert not (bundle / "runs").exists()
    assert not (bundle / "computational/generations/older").exists()
    target = tmp_path / "restored"
    restored = import_checkpoint(bundle, target, _expectation(context))
    assert restored.library_revision == 10
    assert (target / "profile.sqlite").is_file()
    assert (target / "computational/current.json").is_file()
    assert not (target / "runs").exists()


def test_checkpoint_rejects_tampering_without_mutating_target(
    workspace, library, settings, tmp_path
):
    ProfileBuilder(workspace, library, settings, FixedVectors()).run()
    bundle = export_checkpoint(workspace / "data", tmp_path / "bundle", _context())
    profile = bundle / "profile.sqlite"
    profile.write_bytes(profile.read_bytes() + b"tamper")
    target = tmp_path / "target"

    with pytest.raises(CheckpointError):
        import_checkpoint(bundle, target, _expectation(_context()))

    assert not (target / "profile.sqlite").exists()
    assert not (target / "computational").exists()


def test_checkpoint_requires_succeeded_closed_namespace(
    workspace, library, settings, tmp_path
):
    ProfileBuilder(workspace, library, settings, FixedVectors()).run()
    bundle = export_checkpoint(workspace / "data", tmp_path / "bundle", _context())
    raw = json.loads((bundle / "checkpoint.json").read_text())
    raw["source_result_status"] = "degraded"
    (bundle / "checkpoint.json").write_text(json.dumps(raw))

    with pytest.raises(CheckpointError):
        import_checkpoint(bundle, tmp_path / "target", _expectation(_context()))


def test_checkpoint_rejects_symlink(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "checkpoint.json").symlink_to(tmp_path / "outside")
    with pytest.raises(CheckpointError):
        import_checkpoint(bundle, tmp_path / "target", _expectation(_context()))
