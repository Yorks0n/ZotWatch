import json
from pathlib import Path

import pytest

from src.build_profile import ProfileBuilder
from src.computational_state import (
    EmbeddingRuntimeDescriptor,
    StateCorruptionError,
    StateExpectation,
    StateFutureSchemaError,
    StateManager,
    StateChangedDuringBuild,
    descriptor_for_vectorizer,
    embedding_input_fingerprint,
    expectation_from_snapshot,
    library_snapshot_fingerprints,
    profile_config_fingerprint,
    pseudonymous_library_identity,
)
from src.models import ZoteroItem
from .helpers import FixedVectors


def descriptor(**changes):
    values = {
        "provider": "local",
        "model_identifier": "synthetic/model",
        "model_revision": "commit-1",
        "artifact_identity": "artifact-config-1",
        "dimension": 3,
        "diagnostic_probe_fingerprint": "probe-a",
    }
    values.update(changes)
    return EmbeddingRuntimeDescriptor(**values)


def expectation(model=None):
    model = model or descriptor()
    return StateExpectation(
        library_identity_sha256="a" * 64,
        library_revision=12,
        library_snapshot_sha256="b" * 64,
        embedding_input_set_sha256="c" * 64,
        item_count=2,
        embedding=model,
        profile_config_sha256=profile_config_fingerprint(model),
    )


def test_hard_model_identity_excludes_raw_probe_diagnostics():
    first = descriptor(diagnostic_probe_fingerprint="raw-runtime-a")
    second = descriptor(diagnostic_probe_fingerprint="raw-runtime-b")
    assert first.hard_fingerprint_sha256 == second.hard_fingerprint_sha256
    assert descriptor(model_revision="commit-2").hard_fingerprint_sha256 != first.hard_fingerprint_sha256
    assert descriptor(artifact_identity="artifact-2").hard_fingerprint_sha256 != first.hard_fingerprint_sha256


def test_profile_fingerprint_uses_semantics_not_runtime_or_ranking_values():
    first = descriptor(diagnostic_probe_fingerprint="runtime-a")
    second = descriptor(diagnostic_probe_fingerprint="runtime-b")
    assert profile_config_fingerprint(first) == profile_config_fingerprint(second)
    assert profile_config_fingerprint(first, semantic_overrides={"top_venues": 10}) != profile_config_fingerprint(first)


def test_library_and_item_fingerprints_are_structured_and_order_stable():
    first = ZoteroItem(
        key="B",
        version=2,
        title="Title",
        abstract="Abstract",
        creators=["A; B"],
        tags=["tag"],
        raw={"data": {"publicationTitle": "Venue"}},
    )
    second = ZoteroItem(key="A", version=1, title="Other")
    assert library_snapshot_fingerprints([first, second]) == library_snapshot_fingerprints([second, first])
    assert embedding_input_fingerprint(first) != embedding_input_fingerprint(
        first.model_copy(update={"tags": ["tag", "new"]})
    )


def test_library_identity_is_pseudonymous_and_stable():
    value = pseudonymous_library_identity("user", "123456")
    assert value == pseudonymous_library_identity("user", "123456")
    assert value != pseudonymous_library_identity("user", "654321")
    assert "123456" not in value


def test_future_and_corrupt_pointer_fail_closed(tmp_path):
    manager = StateManager(tmp_path)
    manager.current_path.parent.mkdir(parents=True)
    manager.current_path.write_text(json.dumps({"schema_version": 2}))
    with pytest.raises(StateFutureSchemaError, match="future"):
        manager.load_current(expectation())

    manager.current_path.write_text("not-json")
    with pytest.raises(StateCorruptionError, match="current pointer"):
        manager.load_current(expectation())


def current_expectation(library, vectorizer=None):
    vectorizer = vectorizer or FixedVectors()
    return expectation_from_snapshot(
        library.read_profile_snapshot(),
        descriptor_for_vectorizer(vectorizer),
    )


def test_published_generation_loads_only_when_expectation_matches(workspace, library, settings):
    vectorizer = FixedVectors()
    artifacts = ProfileBuilder(workspace, library, settings, vectorizer).run()
    manager = StateManager(workspace / "data")

    handle = manager.load_current(current_expectation(library, vectorizer))
    assert handle.generation_id == artifacts.generation_id
    assert handle.manifest.engine.version

    other = descriptor(model_identifier="other")
    with pytest.raises(Exception, match="embedding model"):
        manager.load_current(
            expectation_from_snapshot(library.read_profile_snapshot(), other)
        )


@pytest.mark.parametrize("artifact", ["profile_path", "embeddings_path", "index_path", "manifest_path"])
def test_missing_generation_artifact_is_corrupt(workspace, library, settings, artifact):
    vectorizer = FixedVectors()
    ProfileBuilder(workspace, library, settings, vectorizer).run()
    manager = StateManager(workspace / "data")
    handle = manager.load_current(current_expectation(library, vectorizer))
    Path(getattr(handle, artifact)).unlink()

    with pytest.raises(StateCorruptionError):
        manager.load_current(current_expectation(library, vectorizer))


def test_dimension_mismatch_is_rejected(workspace, library, settings):
    vectorizer = FixedVectors()
    ProfileBuilder(workspace, library, settings, vectorizer).run()
    wrong = descriptor_for_vectorizer(vectorizer)
    wrong = EmbeddingRuntimeDescriptor(
        **{**wrong.__dict__, "dimension": 4}
    )
    with pytest.raises(Exception, match="embedding model|dimension"):
        StateManager(workspace / "data").load_current(
            expectation_from_snapshot(library.read_profile_snapshot(), wrong)
        )


def test_library_change_during_generation_does_not_publish(workspace, library, settings, monkeypatch):
    manager = StateManager(workspace / "data")
    original_validate = manager._validate_staged

    def change_revision(*args, **kwargs):
        original_validate(*args, **kwargs)
        library.set_last_modified_version(11)

    monkeypatch.setattr(manager, "_validate_staged", change_revision)
    with pytest.raises(StateChangedDuringBuild):
        ProfileBuilder(
            workspace,
            library,
            settings,
            FixedVectors(),
            state_manager=manager,
        ).run()
    assert not manager.current_path.exists()
    assert list(manager.staging_dir.iterdir()) == []


def test_pointer_swap_failure_preserves_previous_generation(
    workspace, library, settings, monkeypatch
):
    from src import computational_state as state_module

    vectorizer = FixedVectors()
    manager = StateManager(workspace / "data")
    ProfileBuilder(
        workspace, library, settings, vectorizer, state_manager=manager
    ).run()
    previous = manager.current_path.read_bytes()
    library.set_last_modified_version(11)

    original_replace = state_module.os.replace

    def fail_pointer(source, destination):
        if Path(destination) == manager.current_path:
            raise OSError("synthetic pointer interruption")
        return original_replace(source, destination)

    monkeypatch.setattr(state_module.os, "replace", fail_pointer)
    with pytest.raises(OSError, match="pointer interruption"):
        ProfileBuilder(
            workspace,
            library,
            settings,
            vectorizer,
            state_manager=manager,
        ).run()
    assert manager.current_path.read_bytes() == previous


def test_coordinator_lease_is_passed_without_nested_acquire(workspace, library, settings):
    manager = StateManager(workspace / "data")
    builder = ProfileBuilder(
        workspace,
        library,
        settings,
        FixedVectors(),
        state_manager=manager,
    )
    with manager.coordinator.acquire() as lease:
        artifacts = builder.run(lease=lease)
        with pytest.raises(RuntimeError, match="non-reentrant"):
            with manager.coordinator.acquire():
                pass
    assert Path(artifacts.manifest_path).is_file()
