from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY
from contextlib import contextmanager

import pytest

from src import cli
from src.build_profile import ProfileBuilder
from src.computational_state import StateCoordinator
from src.models import ZoteroItem
from src.score_rank import WorkRanker
from .helpers import FixedVectors


class OtherFixedVectors(FixedVectors):
    model_name = "synthetic-e4-other-model"
    model_revision = "other-revision"
    artifact_identity = "other-artifact"


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
