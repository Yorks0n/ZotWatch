import pytest
import requests

from src import ingest_zotero_api as ingest
from src.dedupe import DedupeEngine, _is_title_in_list
from src.models import CandidateWork, ZoteroItem
from .helpers import FIXTURES, Response, ids, read_json


def test_client_paging_and_304(settings, monkeypatch):
    calls = []
    replies = iter([Response([], headers={"Link": '<https://api.zotero.org/users/123456/items?start=100>; rel="next"'}), Response([])])
    def request(session, method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert session.headers["Authorization"] == "Bearer synthetic-zotero-key"
        return next(replies)
    monkeypatch.setattr(ingest, "request_with_retry", request)
    client = ingest.ZoteroClient(settings)
    assert len(list(client.iter_items(10))) == 2
    assert calls[0][2]["headers"] == {"If-Modified-Since-Version": "10"}
    assert calls[0][2]["params"] == {
        "limit": 100,
        "sort": "dateAdded",
        "direction": "asc",
        "includeTrashed": 1,
        "since": 10,
    }
    assert calls[1][2]["params"] is None
    assert calls[1][2]["headers"] == {}
    monkeypatch.setattr(ingest, "request_with_retry", lambda *a, **kw: Response(status=304))
    assert list(client.iter_items(10)) == []


def test_e4_content_update_invalidates_legacy_embedding(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    library.set_embedding("LIB1", b"old-embedding")
    rows = read_json(FIXTURES / "zotero.json")
    rows[0]["version"] = 20
    rows[0]["data"].update(version=20, title="Updated genome")
    replies = iter([
        Response([rows[0]], headers={"Last-Modified-Version": "20"}),
        Response({"items": []}, headers={"Last-Modified-Version": "20"}),
    ])
    def request(*args, **kwargs):
        return next(replies)
    monkeypatch.setattr(ingest, "request_with_retry", request)
    stats = ingest.ZoteroIngestor(library, settings).run()
    assert (stats.fetched, stats.updated, stats.removed) == (1, 1, 0)
    assert library.last_modified_version() == 20
    assert next(library.iter_items()).title == "Updated genome"
    assert library.fetch_all_embeddings() == []


def test_e3_full_sync_removes_absent_rows(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    rows = read_json(FIXTURES / "zotero.json")
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return Response([rows[0]], headers={"Last-Modified-Version": "20"})
    monkeypatch.setattr(ingest, "request_with_retry", request)
    stats = ingest.ZoteroIngestor(library, settings).run(full=True)
    assert len(calls) == 1  # Full does not request tombstones.
    assert calls[0]["headers"] == {}
    assert stats.removed == stats.applied_removed == 1
    assert [item.key for item in library.iter_items()] == ["LIB1"]


def test_304_is_successful_noop_without_deleted_request(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    calls = []
    def request(session, method, url, **kw):
        calls.append((url, kw))
        return Response({"items": ["LIB2"]}) if url.endswith("/deleted") else Response(status=304)
    monkeypatch.setattr(ingest, "request_with_retry", request)
    stats = ingest.ZoteroIngestor(library, settings).run()
    assert (stats.fetched, stats.updated, stats.removed) == (0, 0, 0)
    assert stats.last_modified_version == stats.committed_revision == 10
    assert len(calls) == 1
    assert library.last_modified_version() == 10


def test_total_network_failure_keeps_existing_state(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    before = [item.model_dump() for item in library.iter_items()]
    def fail(*a, **kw):
        raise requests.Timeout("synthetic timeout")
    monkeypatch.setattr(ingest, "request_with_retry", fail)
    with pytest.raises(ingest.ZoteroSyncError):
        ingest.ZoteroIngestor(library, settings).run()
    assert library.last_modified_version() == 10
    assert [item.model_dump() for item in library.iter_items()] == before


def test_item_mapping_and_unchanged_upsert_count(library, settings, monkeypatch):
    rows = read_json(FIXTURES / "zotero.json")
    item = ZoteroItem.from_zotero_api(rows[0])
    assert item.content_for_embedding() == "Genome atlas\nSynthetic cells.\nAda Example\ngenomics"
    assert item.year == 2020
    runner = ingest.ZoteroIngestor(library, settings)
    monkeypatch.setattr(runner.client, "iter_items", lambda **kw: iter([Response(rows, headers={"Last-Modified-Version": "10"})]))
    monkeypatch.setattr(
        runner.client,
        "fetch_deleted",
        lambda *a, **kw: Response({"items": []}, headers={"Last-Modified-Version": "10"}),
    )
    stats = runner.run()
    assert stats.updated == stats.fetched == 2  # Includes unchanged records.


def work(identifier, title, doi=None):
    return CandidateWork(source="crossref", identifier=identifier, title=title, doi=doi)


def test_dedupe_library_and_batch_identifiers_titles(library):
    works = [work("duplicate-doi", "Unrelated", " 10.0000/LIBRARY "),
             work("HTTPS://EXAMPLE.INVALID/LIBRARY", "Other unrelated"),
             work("title-match", "  atlas  GENOME "),
             work("new", "Quantum crystal", "10.0000/new"),
             work("NEW", "Different rocks"), work("doi-match", "Different oceans", "10.0000/NEW"),
             work("fuzzy", "Quantum crystal spectroscopy"),
             work("unique", "Volcanic minerals")]
    assert ids(DedupeEngine(library).filter(works)) == ["new", "unique"]


def test_doi_url_prefix_is_not_normalized(library):
    assert ids(DedupeEngine(library).filter([work("prefix", "Volcanic minerals", "https://doi.org/10.0000/library")])) == ["prefix"]


@pytest.mark.parametrize("threshold,duplicate", [(.9, True), (.900001, False), (1, False)])
def test_fuzzy_threshold_boundary(threshold, duplicate):
    assert _is_title_in_list("alpha beta", ["alpha zeta"], threshold) is duplicate
