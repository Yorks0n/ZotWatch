from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY
from contextlib import contextmanager

import pytest
import requests

from src import cli, ingest_zotero_api as ingest
from src.build_profile import ProfileBuilder
from src.computational_state import StateCoordinator
from src.computational_state import (
    StateCompatibilityError,
    StateFutureSchemaError,
    StateManager,
    descriptor_for_vectorizer,
    expectation_from_snapshot,
)
from src.models import ZoteroItem
from src.score_rank import WorkRanker
from .helpers import FIXTURES, FixedVectors, Response, read_json


class OtherFixedVectors(FixedVectors):
    model_name = "synthetic-e4-other-model"
    model_revision = "other-revision"
    artifact_identity = "other-artifact"


class CountingVectors(FixedVectors):
    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        return super().encode(texts)


def ensure(workspace, library, settings, vectorizer, manager=None, *, force=False):
    manager = manager or StateManager(workspace / "data")
    with manager.coordinator.acquire() as lease:
        return cli._ensure_computational_state(
            workspace,
            settings,
            library,
            state_root=workspace / "data",
            manager=manager,
            vectorizer=vectorizer,
            lease=lease,
            force=force,
        )


def test_profile_build_publishes_versioned_generation(workspace, library, settings):
    library.set_metadata("library_identity_sha256", "a" * 64)
    library.set_last_modified_version(10)

    artifacts = ProfileBuilder(workspace, library, settings, FixedVectors()).run()

    manifest_path = Path(artifacts.manifest_path)
    assert manifest_path.name == "state-manifest.json"
    assert manifest_path.parent.parent.name == "generations"
    assert Path(artifacts.profile_json_path).parent == manifest_path.parent
    assert Path(artifacts.faiss_path).parent == manifest_path.parent
    assert (workspace / "data/computational/current.json").is_file()


def test_content_change_invalidates_legacy_embedding(storage):
    original = ZoteroItem(key="K1", version=1, title="Before")
    storage.apply_zotero_sync([(original, "before")], [], full=True, revision=1)
    storage.set_embedding("K1", b"legacy-vector")

    changed = original.model_copy(update={"version": 2, "title": "After"})
    storage.apply_zotero_sync([(changed, "after")], [], full=False, revision=2)

    assert storage.fetch_all_embeddings() == []


def test_ranker_rejects_generation_from_another_model(workspace, library, settings):
    library.set_metadata("library_identity_sha256", "a" * 64)
    library.set_last_modified_version(10)
    ProfileBuilder(workspace, library, settings, FixedVectors()).run()

    with pytest.raises(Exception, match="(?i)model|compatib|state"):
        WorkRanker(workspace, settings, OtherFixedVectors())


def test_watch_ensures_state_after_sync(workspace, settings, storage, monkeypatch):
    events = []
    coordinator = StateCoordinator(workspace / "data")
    acquisitions = 0
    acquire = coordinator.acquire

    @contextmanager
    def counted_acquire():
        nonlocal acquisitions
        acquisitions += 1
        with acquire() as lease:
            yield lease

    monkeypatch.setattr(coordinator, "acquire", counted_acquire)
    monkeypatch.setattr(cli, "StateCoordinator", lambda *a, **kw: coordinator)
    storage.upsert_item(ZoteroItem(key="K1", version=1, title="Genome atlas"), "content")
    storage.set_library_identity_sha256(cli._library_identity(settings))
    storage.set_last_modified_version(10)
    monkeypatch.setattr(
        cli,
        "ZoteroIngestor",
        lambda *a: SimpleNamespace(
            run=lambda **kw: events.append(("sync", kw))
            or SimpleNamespace(committed_revision=10)
        ),
    )
    monkeypatch.setattr(cli.build_profile_module, "TextVectorizer", FixedVectors)
    monkeypatch.setattr(cli, "CandidateFetcher", lambda *a, **kw: SimpleNamespace(fetch_all=list))
    monkeypatch.setattr(cli, "DedupeEngine", lambda *a: SimpleNamespace(filter=lambda works: works))
    monkeypatch.setattr(cli, "WorkRanker", lambda *a, **kw: SimpleNamespace(rank=lambda works: works))

    cli.run_watch(workspace, settings, storage, rss=False, report=False, top=10, push=False)

    assert events == [
        (
            "sync",
            {
                "full": False,
                "library_identity_sha256": cli._library_identity(settings),
                "lease": ANY,
            },
        )
    ]
    assert (workspace / "data/computational/current.json").is_file()
    assert acquisitions == 1


def test_unchanged_library_reuses_generation_without_encoding(workspace, library, settings):
    vectorizer = CountingVectors()
    first, _ = ensure(workspace, library, settings, vectorizer)
    second, _ = ensure(workspace, library, settings, vectorizer)
    assert first.generation_id == second.generation_id
    assert vectorizer.calls == 1


def test_delete_and_restore_publish_matching_generations(workspace, library, settings):
    vectorizer = FixedVectors()
    first, _ = ensure(workspace, library, settings, vectorizer)
    items = list(library.iter_items())

    library.apply_zotero_sync(
        [(items[0], "first")],
        [],
        full=True,
        revision=11,
    )
    deleted, _ = ensure(workspace, library, settings, vectorizer)
    assert deleted.generation_id != first.generation_id
    assert deleted.manifest.library.item_count == 1

    library.apply_zotero_sync(
        [(item, f"restored-{item.key}") for item in items],
        [],
        full=True,
        revision=12,
    )
    restored, _ = ensure(workspace, library, settings, vectorizer)
    assert restored.generation_id != deleted.generation_id
    assert restored.manifest.library.item_count == 2


def test_corrupt_current_index_is_rebuilt(workspace, library, settings):
    vectorizer = FixedVectors()
    first, _ = ensure(workspace, library, settings, vectorizer)
    first.index_path.unlink()
    second, _ = ensure(workspace, library, settings, vectorizer)
    assert second.generation_id != first.generation_id
    assert second.index_path.is_file()


def test_failed_rebuild_preserves_but_cannot_rank_previous_state(
    workspace, library, settings
):
    first, _ = ensure(workspace, library, settings, FixedVectors())
    pointer_before = (workspace / "data/computational/current.json").read_bytes()
    library.set_last_modified_version(11)

    class FailingVectors(FixedVectors):
        def encode(self, texts):
            raise RuntimeError("synthetic generation failure")

    with pytest.raises(RuntimeError, match="generation failure"):
        ensure(workspace, library, settings, FailingVectors())
    assert (workspace / "data/computational/current.json").read_bytes() == pointer_before

    manager = StateManager(workspace / "data")
    current = expectation_from_snapshot(
        library.read_profile_snapshot(), descriptor_for_vectorizer(FixedVectors())
    )
    with pytest.raises(StateCompatibilityError, match="library revision"):
        manager.load_current(current)
    assert first.generation_dir.is_dir()


def test_future_pointer_requires_explicit_forced_rebuild(workspace, library, settings):
    handle, _ = ensure(workspace, library, settings, FixedVectors())
    manager = StateManager(workspace / "data")
    manager.current_path.write_text('{"schema_version": 2}')

    with pytest.raises(StateFutureSchemaError):
        ensure(workspace, library, settings, FixedVectors(), manager)
    rebuilt, _ = ensure(
        workspace, library, settings, FixedVectors(), manager, force=True
    )
    assert rebuilt.generation_id != handle.generation_id


def test_missing_library_identity_is_established_only_by_successful_full_sync(
    storage, settings, monkeypatch
):
    old = ZoteroItem(key="OLD", version=10, title="old")
    storage.upsert_item(old, "old")
    storage.set_last_modified_version(10)
    identity = cli._library_identity(settings)
    rows = read_json(FIXTURES / "zotero.json")
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs)
        return Response(rows, headers={"Last-Modified-Version": "20"})

    monkeypatch.setattr(ingest, "request_with_retry", request)
    ingest.ZoteroIngestor(storage, settings).run(
        library_identity_sha256=identity
    )
    assert "since" not in calls[0]["params"]
    assert calls[0]["params"]["includeTrashed"] == 1
    assert storage.library_identity_sha256() == identity
    assert storage.last_modified_version() == 20
    assert "OLD" not in {item.key for item in storage.iter_items()}


def test_failed_identity_rebind_preserves_old_mirror(storage, settings, monkeypatch):
    old = ZoteroItem(key="OLD", version=10, title="old")
    storage.upsert_item(old, "old")
    storage.set_last_modified_version(10)

    def fail(*args, **kwargs):
        raise requests.Timeout("synthetic identity sync failure")

    monkeypatch.setattr(ingest, "request_with_retry", fail)
    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(storage, settings).run(
            library_identity_sha256=cli._library_identity(settings)
        )
    assert storage.library_identity_sha256() is None
    assert storage.last_modified_version() == 10
    assert [item.key for item in storage.iter_items()] == ["OLD"]
