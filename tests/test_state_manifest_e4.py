import json

import pytest

from src.computational_state import (
    EmbeddingRuntimeDescriptor,
    StateCorruptionError,
    StateExpectation,
    StateFutureSchemaError,
    StateManager,
    embedding_input_fingerprint,
    library_snapshot_fingerprints,
    profile_config_fingerprint,
    pseudonymous_library_identity,
)
from src.models import ZoteroItem


def descriptor(**changes):
    values = {
        "provider": "local",
        "model_identifier": "synthetic/model",
        "model_revision": "commit-1",
        "artifact_identity": "artifact-config-1",
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
