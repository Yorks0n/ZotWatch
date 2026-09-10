from copy import deepcopy
from dataclasses import asdict

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
    assert calls[0][2]["params"] == {"limit": 100, "sort": "dateAdded", "direction": "asc"}
    assert calls[1][2]["params"] is None
    assert calls[1][2]["headers"] == {}
    monkeypatch.setattr(ingest, "request_with_retry", lambda *a, **kw: Response(status=304))
    assert list(client.iter_items(10)) == []


@pytest.mark.parametrize("partial", [False, True])
def test_bug_ingest_deletion_watermark_and_partial_progress(library, settings, monkeypatch, partial):
    # BUG-I1/I2: deletion query uses new watermark, even after page failure.
    library.set_last_modified_version(10)
    library.set_embedding("LIB1", b"old-embedding")
    rows = read_json(FIXTURES / "zotero.json")
    rows[0]["data"].update(version=20, title="Updated genome")
    new = deepcopy(rows[1])
    new["data"].update(key="LIB3", version=20, title="New synthetic work")
    calls = []
    def request(session, method, url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/deleted"):
            return Response({"items": ["LIB2"]})
        if "start=" in url:
            if partial:
                raise requests.ConnectionError("synthetic page failure")
            return Response([new], headers={"Last-Modified-Version": "20"})
        return Response([rows[0]], headers={"Last-Modified-Version": "20", "Link": '<https://api.zotero.org/users/123456/items?start=100>; rel="next"'})
    monkeypatch.setattr(ingest, "request_with_retry", request)
    stats = ingest.ZoteroIngestor(library, settings).run()
    assert asdict(stats) == dict(fetched=1 if partial else 2, updated=1 if partial else 2, removed=1, last_modified_version=20)
    assert calls[-1][1]["params"] == {"since": 20}
    assert library.last_modified_version() == 20
    assert [item.key for item in library.iter_items()] == (["LIB1"] if partial else ["LIB1", "LIB3"])
    assert next(library.iter_items()).title == "Updated genome"
    # BUG-I4: changing content does not invalidate its stored embedding.
    assert library.fetch_all_embeddings() == [("LIB1", b"old-embedding")]


def test_bug_full_sync_retains_absent_rows(library, settings, monkeypatch):
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
    assert stats.removed == 0
    assert [item.key for item in library.iter_items()] == ["LIB1", "LIB2"]


def test_304_still_fetches_deletions(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    calls = []
    def request(session, method, url, **kw):
        calls.append((url, kw))
        return Response({"items": ["LIB2"]}) if url.endswith("/deleted") else Response(status=304)
    monkeypatch.setattr(ingest, "request_with_retry", request)
    stats = ingest.ZoteroIngestor(library, settings).run()
    assert asdict(stats) == dict(fetched=0, updated=0, removed=1, last_modified_version=None)
    assert calls[-1][1]["params"] == {"since": 10}
    assert library.last_modified_version() == 10


def test_total_network_failure_keeps_existing_state(library, settings, monkeypatch):
    library.set_last_modified_version(10)
    def fail(*a, **kw):
        raise requests.Timeout("synthetic timeout")
    monkeypatch.setattr(ingest, "request_with_retry", fail)
    stats = ingest.ZoteroIngestor(library, settings).run()
    assert asdict(stats) == dict(fetched=0, updated=0, removed=0, last_modified_version=None)
    assert library.last_modified_version() == 10
    assert len(list(library.iter_items())) == 2


def test_item_mapping_and_unchanged_upsert_count(library, settings, monkeypatch):
    rows = read_json(FIXTURES / "zotero.json")
    item = ZoteroItem.from_zotero_api(rows[0])
    assert item.content_for_embedding() == "Genome atlas\nSynthetic cells.\nAda Example\ngenomics"
    assert item.year == 2020
    runner = ingest.ZoteroIngestor(library, settings)
    monkeypatch.setattr(runner.client, "iter_items", lambda **kw: iter([Response(rows, headers={"Last-Modified-Version": "10"})]))
    monkeypatch.setattr(runner.client, "fetch_deleted", lambda **kw: [])
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
