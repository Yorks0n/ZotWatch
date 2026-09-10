"""E3 Zotero mirror correctness regressions.

These tests use synthetic Zotero responses and a real temporary SQLite database.
The first commit intentionally fails against v2-e2-baseline production code.
"""

from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest
import requests

from src import ingest_zotero_api as ingest
from src.models import ZoteroItem
from src.utils import hash_content
from .helpers import FIXTURES, Response, read_json


def remote_item(key: str, version: int, title: str, *, deleted: bool = False) -> dict:
    data = {
        "key": key,
        "version": version,
        "itemType": "journalArticle",
        "title": title,
        "abstractNote": "Synthetic abstract",
        "creators": [],
        "tags": [],
        "collections": [],
        "date": "2026",
        "DOI": f"10.0000/{key.lower()}",
        "url": f"https://example.invalid/{key.lower()}",
    }
    if deleted:
        # Zotero Web API v3 emits integer 1 for an item in trash.
        data["deleted"] = 1
    return {"key": key, "version": version, "data": data}


def response(payload, version: int, *, link: str | None = None) -> Response:
    headers = {"Last-Modified-Version": str(version)}
    if link:
        headers["Link"] = f'<{link}>; rel="next"'
    return Response(payload, headers=headers)


class ScriptedRequests:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, session, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.replies:
            raise AssertionError(f"unexpected Zotero request: {method} {url}")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def mirror_state(storage):
    rows = storage.connect().execute(
        """
        SELECT key, version, title, abstract, creators, tags, collections,
               year, doi, url, raw_json, content_hash, embedding
        FROM items ORDER BY key
        """
    ).fetchall()
    return [tuple(row) for row in rows], storage.get_metadata("last_modified_version")


def keys(storage) -> list[str]:
    return sorted(item.key for item in storage.iter_items())


def assert_success_revisions(stats, start: int | None, target: int) -> None:
    assert stats.start_revision == start
    assert stats.target_revision == target
    assert stats.observed_revision == target
    assert stats.committed_revision == target
    assert stats.last_modified_version == target


def assert_incremental_query(call, start: int) -> None:
    _, url, kwargs = call
    assert url.endswith("/items")
    assert kwargs["params"] == {
        "limit": 100,
        "sort": "dateAdded",
        "direction": "asc",
        "since": start,
        "includeTrashed": 1,
    }
    assert kwargs["headers"] == {"If-Modified-Since-Version": str(start)}


def test_initial_sync_without_watermark_establishes_full_active_mirror(storage, settings, monkeypatch):
    storage.upsert_item(ZoteroItem.from_zotero_api(remote_item("STALE", 3, "Stale")))
    script = ScriptedRequests(
        response(
            [remote_item("ACTIVE", 20, "Active"), remote_item("TRASHED", 20, "Trashed", deleted=True)],
            20,
        )
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(storage, settings).run()

    assert keys(storage) == ["ACTIVE"]
    assert storage.last_modified_version() == 20
    assert script.calls[0][2]["params"]["includeTrashed"] == 1
    assert "since" not in script.calls[0][2]["params"]
    assert_success_revisions(stats, None, 20)


def test_noop_304_keeps_committed_revision_and_skips_deleted(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    before = mirror_state(library)
    script = ScriptedRequests(Response(status=304))
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(library, settings).run()

    assert mirror_state(library) == before
    assert len(script.calls) == 1
    assert_incremental_query(script.calls[0], 10)
    assert_success_revisions(stats, 10, 10)
    assert (stats.fetched, stats.updated, stats.removed) == (0, 0, 0)
    assert (stats.applied_inserted, stats.applied_changed, stats.applied_removed) == (0, 0, 0)


def test_incremental_one_added_item(storage, settings, monkeypatch):
    storage.set_last_modified_version(10)
    script = ScriptedRequests(
        response([remote_item("ADDED", 20, "Added")], 20),
        response({"items": []}, 20),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(storage, settings).run()

    assert keys(storage) == ["ADDED"]
    assert (stats.fetched, stats.updated, stats.removed) == (1, 1, 0)
    assert (stats.applied_inserted, stats.applied_changed, stats.applied_removed) == (1, 0, 0)
    assert_success_revisions(stats, 10, 20)


def test_incremental_one_modified_item(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    modified = remote_item("LIB1", 20, "Modified title")
    script = ScriptedRequests(response([modified], 20), response({"items": []}, 20))
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(library, settings).run()

    assert next(item for item in library.iter_items() if item.key == "LIB1").title == "Modified title"
    assert (stats.fetched, stats.updated, stats.removed) == (1, 1, 0)
    assert (stats.applied_inserted, stats.applied_changed, stats.applied_removed) == (0, 1, 0)
    assert_success_revisions(stats, 10, 20)


def test_incremental_one_permanently_deleted_item(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    script = ScriptedRequests(response([], 20), response({"items": ["LIB2"]}, 20))
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(library, settings).run()

    assert script.calls[1][2]["params"] == {"since": 10}
    assert keys(library) == ["LIB1"]
    assert (stats.fetched, stats.updated, stats.removed) == (0, 0, 1)
    assert (stats.applied_inserted, stats.applied_changed, stats.applied_removed) == (0, 0, 1)
    assert_success_revisions(stats, 10, 20)


def test_incremental_add_modify_delete_uses_start_revision(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    original = read_json(FIXTURES / "zotero.json")[0]
    modified = deepcopy(original)
    modified["version"] = 20
    modified["data"].update(version=20, title="Updated genome")
    added = remote_item("LIB3", 20, "New work")
    script = ScriptedRequests(
        response([modified, added], 20),
        response({"items": ["LIB2"]}, 20),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(library, settings).run()

    assert_incremental_query(script.calls[0], 10)
    assert script.calls[1][1].endswith("/deleted")
    assert script.calls[1][2]["params"] == {"since": 10}
    assert keys(library) == ["LIB1", "LIB3"]
    assert next(item for item in library.iter_items() if item.key == "LIB1").title == "Updated genome"
    assert_success_revisions(stats, 10, 20)
    assert (stats.fetched, stats.updated, stats.removed) == (2, 2, 1)
    assert (stats.applied_inserted, stats.applied_changed, stats.applied_removed) == (1, 1, 1)


def test_existing_item_moved_to_trash_is_removed_and_revision_committed(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    script = ScriptedRequests(
        response([remote_item("LIB1", 20, "Genome atlas", deleted=True)], 20),
        response({"items": []}, 20),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(library, settings).run()

    assert_incremental_query(script.calls[0], 10)
    assert keys(library) == ["LIB2"]
    assert_success_revisions(stats, 10, 20)
    assert (stats.fetched, stats.updated, stats.removed) == (1, 0, 1)
    assert stats.applied_removed == 1


def test_trashed_item_restored_reenters_local_mirror(storage, settings, monkeypatch):
    storage.set_last_modified_version(10)
    script = ScriptedRequests(
        response([remote_item("RESTORED", 20, "Restored work")], 20),
        response({"items": []}, 20),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(storage, settings).run()

    assert keys(storage) == ["RESTORED"]
    assert_success_revisions(stats, 10, 20)
    assert (stats.fetched, stats.updated, stats.removed) == (1, 1, 0)
    assert stats.applied_inserted == 1


def test_full_sync_excludes_trash_and_removes_stale_rows(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    script = ScriptedRequests(
        response(
            [
                remote_item("LIB1", 20, "Current genome"),
                remote_item("LIB2", 20, "Trashed ocean", deleted=True),
                remote_item("LIB3", 20, "New active"),
            ],
            20,
        )
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(library, settings).run(full=True)

    assert len(script.calls) == 1
    assert script.calls[0][2]["params"]["includeTrashed"] == 1
    assert keys(library) == ["LIB1", "LIB3"]
    assert_success_revisions(stats, 10, 20)
    assert stats.applied_removed == 1


def test_multi_page_sync_commits_only_after_deleted_response(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    next_url = "https://api.zotero.org/users/123456/items?start=100&since=10&includeTrashed=1"
    script = ScriptedRequests(
        response([remote_item("LIB3", 20, "Page one")], 20, link=next_url),
        response([remote_item("LIB4", 20, "Page two")], 20),
        response({"items": ["LIB2"]}, 20),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(library, settings).run()

    assert keys(library) == ["LIB1", "LIB3", "LIB4"]
    assert [call[1] for call in script.calls] == [
        "https://api.zotero.org/users/123456/items",
        next_url,
        "https://api.zotero.org/users/123456/deleted",
    ]
    assert_success_revisions(stats, 10, 20)


def test_middle_page_failure_leaves_rows_and_watermark_unchanged(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    before = mirror_state(library)
    next_url = "https://api.zotero.org/users/123456/items?start=100"
    script = ScriptedRequests(
        response([remote_item("LIB3", 20, "Partial")], 20, link=next_url),
        requests.ConnectionError("synthetic middle-page failure"),
        response({"items": []}, 20),  # Old implementation incorrectly continues here.
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(library, settings).run()

    assert mirror_state(library) == before


def test_full_middle_page_failure_does_not_replace_existing_mirror(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    before = mirror_state(library)
    next_url = "https://api.zotero.org/users/123456/items?start=100"
    script = ScriptedRequests(
        response([remote_item("FIRST", 20, "First page")], 20, link=next_url),
        requests.ConnectionError("synthetic full middle-page failure"),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(library, settings).run(full=True)

    assert mirror_state(library) == before


def test_deleted_endpoint_failure_leaves_rows_and_watermark_unchanged(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    before = mirror_state(library)
    script = ScriptedRequests(
        response([remote_item("LIB3", 20, "Staged")], 20),
        requests.Timeout("synthetic deleted-endpoint failure"),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(library, settings).run()

    assert mirror_state(library) == before


def test_retry_after_failed_sync_replays_from_original_watermark(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    before = mirror_state(library)
    first = ScriptedRequests(
        response([remote_item("LIB3", 20, "Staged")], 20),
        requests.Timeout("synthetic deleted-endpoint failure"),
    )
    monkeypatch.setattr(ingest, "request_with_retry", first)
    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(library, settings).run()
    assert mirror_state(library) == before

    second = ScriptedRequests(
        response([remote_item("LIB3", 20, "Staged")], 20),
        response({"items": ["LIB2"]}, 20),
    )
    monkeypatch.setattr(ingest, "request_with_retry", second)
    stats = ingest.ZoteroIngestor(library, settings).run()

    assert_incremental_query(second.calls[0], 10)
    assert keys(library) == ["LIB1", "LIB3"]
    assert_success_revisions(stats, 10, 20)


def test_duplicate_replayed_page_is_idempotent_and_preserves_legacy_updated(storage, settings, monkeypatch):
    storage.set_last_modified_version(10)
    duplicate = remote_item("DUPLICATE", 20, "One row")
    next_url = "https://api.zotero.org/users/123456/items?start=100"
    script = ScriptedRequests(
        response([duplicate], 20, link=next_url),
        response([deepcopy(duplicate)], 20),
        response({"items": []}, 20),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(storage, settings).run()

    assert keys(storage) == ["DUPLICATE"]
    assert (stats.fetched, stats.updated) == (2, 2)
    assert (stats.applied_inserted, stats.applied_changed) == (1, 0)


def test_remote_revision_change_discards_attempt_and_restarts(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    first_next = "https://api.zotero.org/users/123456/items?start=100"
    script = ScriptedRequests(
        response([remote_item("LIB3", 20, "Attempt one")], 20, link=first_next),
        response([remote_item("LIB4", 21, "Drift")], 21),
        response([remote_item("LIB3", 22, "Stable"), remote_item("LIB4", 22, "Stable too")], 22),
        response({"items": ["LIB2"]}, 22),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(library, settings).run()

    first_item_calls = [call for call in script.calls if call[1].endswith("/items")]
    assert len(first_item_calls) == 2
    assert all(call[2]["params"]["since"] == 10 for call in first_item_calls)
    assert keys(library) == ["LIB1", "LIB3", "LIB4"]
    assert_success_revisions(stats, 10, 22)


def test_repeated_remote_revision_drift_exhausts_bound_and_keeps_state(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    before = mirror_state(library)
    next_url = "https://api.zotero.org/users/123456/items?start=100"
    script = ScriptedRequests(
        response([remote_item("A", 20, "A")], 20, link=next_url),
        response([remote_item("B", 21, "B")], 21),
        response([remote_item("A", 22, "A")], 22, link=next_url),
        response([remote_item("B", 23, "B")], 23),
        response([remote_item("A", 24, "A")], 24, link=next_url),
        response([remote_item("B", 25, "B")], 25),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    with pytest.raises(ingest.ZoteroRevisionChanged):
        ingest.ZoteroIngestor(library, settings).run()

    base_calls = [call for call in script.calls if call[1].endswith("/items")]
    assert len(base_calls) == ingest.REVISION_RESTART_ATTEMPTS == 3
    assert all(call[2]["params"]["since"] == 10 for call in base_calls)
    assert mirror_state(library) == before


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"unexpected": "object instead of item list"},
        [remote_item("GOOD", 20, "Good"), {"data": {"version": 20, "title": "Missing key"}}],
        [remote_item("GOOD", 20, "Good"), remote_item("BADTRASH", 20, "Bad trash")],
    ],
    ids=["wrong-page-shape", "missing-item-key", "invalid-trash-marker"],
)
def test_malformed_item_or_payload_aborts_whole_sync(storage, settings, monkeypatch, bad_payload):
    storage.set_last_modified_version(10)
    if isinstance(bad_payload, list) and bad_payload[-1].get("data", {}).get("key") == "BADTRASH":
        bad_payload[-1]["data"]["deleted"] = "yes"
    before = mirror_state(storage)
    script = ScriptedRequests(response(bad_payload, 20), response({"items": []}, 20))
    monkeypatch.setattr(ingest, "request_with_retry", script)

    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(storage, settings).run()

    assert mirror_state(storage) == before


@pytest.mark.parametrize("header", [{}, {"Last-Modified-Version": "not-an-int"}])
def test_missing_or_invalid_revision_header_aborts_sync(storage, settings, monkeypatch, header):
    storage.set_last_modified_version(10)
    before = mirror_state(storage)
    script = ScriptedRequests(Response([remote_item("GOOD", 20, "Good")], headers=header))
    monkeypatch.setattr(ingest, "request_with_retry", script)

    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(storage, settings).run()

    assert mirror_state(storage) == before


def test_pagination_cycle_aborts_without_writes(storage, settings, monkeypatch):
    storage.set_last_modified_version(10)
    before = mirror_state(storage)
    repeated = "https://api.zotero.org/users/123456/items?start=100"
    script = ScriptedRequests(
        response([remote_item("GOOD", 20, "Good")], 20, link=repeated),
        response([remote_item("OTHER", 20, "Other")], 20, link=repeated),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(storage, settings).run()

    assert mirror_state(storage) == before


def test_valid_empty_full_snapshot_removes_rows_only_after_complete_response(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    script = ScriptedRequests(response([], 20))
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(library, settings).run(full=True)

    assert keys(library) == []
    assert library.last_modified_version() == 20
    assert stats.applied_removed == 2


def test_full_sync_network_failure_preserves_existing_database(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    before = mirror_state(library)
    script = ScriptedRequests(requests.Timeout("synthetic full-sync failure"))
    monkeypatch.setattr(ingest, "request_with_retry", script)

    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(library, settings).run(full=True)

    assert mirror_state(library) == before


def test_sqlite_failure_rolls_back_items_deletions_and_watermark(storage, settings, monkeypatch):
    storage.set_last_modified_version(10)
    storage.connect().execute(
        """
        CREATE TRIGGER fail_e3_insert BEFORE INSERT ON items
        WHEN NEW.key = 'FAIL'
        BEGIN SELECT RAISE(ABORT, 'synthetic E3 apply failure'); END
        """
    )
    storage.connect().commit()
    before = mirror_state(storage)
    script = ScriptedRequests(
        response([remote_item("GOOD", 20, "Good"), remote_item("FAIL", 20, "Fail")], 20),
        response({"items": []}, 20),
    )
    monkeypatch.setattr(ingest, "request_with_retry", script)

    with pytest.raises(sqlite3.DatabaseError):
        ingest.ZoteroIngestor(storage, settings).run()

    assert mirror_state(storage) == before


def test_unknown_tombstone_preserves_legacy_removed_but_applied_count_is_zero(storage, settings, monkeypatch):
    storage.set_last_modified_version(10)
    script = ScriptedRequests(response([], 20), response({"items": ["UNKNOWN"]}, 20))
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(storage, settings).run()

    assert stats.removed == 1
    assert stats.applied_removed == 0
    assert_success_revisions(stats, 10, 20)


def test_unchanged_replay_counts_legacy_updated_without_actual_change(storage, settings, monkeypatch):
    unchanged = remote_item("UNCHANGED", 10, "Same")
    item = ZoteroItem.from_zotero_api(unchanged)
    content_hash = hash_content(
        item.title,
        item.abstract or "",
        ",".join(item.creators),
        ",".join(item.tags),
    )
    storage.upsert_item(item, content_hash=content_hash)
    storage.set_last_modified_version(10)
    script = ScriptedRequests(response([unchanged], 20), response({"items": []}, 20))
    monkeypatch.setattr(ingest, "request_with_retry", script)

    stats = ingest.ZoteroIngestor(storage, settings).run()

    assert stats.updated == 1
    assert (stats.applied_inserted, stats.applied_changed, stats.applied_removed) == (0, 0, 0)
    assert_success_revisions(stats, 10, 20)
