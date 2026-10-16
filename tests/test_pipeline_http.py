import logging
import socket
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest
import numpy as np
import requests

from src import cli, build_profile, score_rank, ingest_zotero_api, fetch_new
from src.http_utils import request_with_retry
from .helpers import FIXTURES, FixedVectors, Response, read_json


class PipelineVectors(FixedVectors):
    def encode(self, texts):
        values = read_json(FIXTURES / "vectors.json")
        return np.asarray(
            [values.get(text.split("\n")[0], [0.0, 0.0, 0.0]) for text in texts],
            dtype="float32",
        )


def test_cli_profile_then_watch_real_pipeline(workspace, settings, candidates, monkeypatch):
    """Real CLI, config, ingestion, storage, profile, index, dedupe, rank and writers."""
    rows = read_json(FIXTURES / "zotero.json")
    replies = iter([Response(rows, headers={"Last-Modified-Version": "10"}),
                    Response([{"data": {"key": "NEW", "version": 20, "title": "New library science"}}], headers={"Last-Modified-Version": "20"}),
                    Response({"items": []}, headers={"Last-Modified-Version": "20"})])
    monkeypatch.setattr(ingest_zotero_api, "request_with_retry", lambda *a, **kw: next(replies))
    monkeypatch.setattr(build_profile, "TextVectorizer", PipelineVectors)
    monkeypatch.setattr(score_rank, "TextVectorizer", PipelineVectors)
    monkeypatch.setattr(fetch_new.CandidateFetcher, "_fetch_public_candidates", lambda *a: candidates)
    monkeypatch.setattr(fetch_new.CandidateFetcher, "_fetch_crossref_top_venues", lambda *a: [])
    cli.main(["profile", "--full", "--base-dir", str(workspace)])
    before_pointer = read_json(workspace / "data/computational/current.json")
    cli.main(["watch", "--rss", "--report", "--top", "3", "--base-dir", str(workspace)])
    after_pointer = read_json(workspace / "data/computational/current.json")
    assert after_pointer["generation_id"] != before_pointer["generation_id"]
    profile = read_json(
        workspace
        / "data/computational/generations"
        / after_pointer["generation_id"]
        / "profile.json"
    )
    assert profile["item_count"] == 3
    from src.storage import ProfileStorage
    db = ProfileStorage(workspace / "data/profile.sqlite")
    try:
        assert len(list(db.iter_items())) == 3
        assert db.last_modified_version() == 20
    finally:
        db.close()
    rss = ET.parse(workspace / "reports/feed.xml")
    assert [node.text for node in rss.findall("channel/item/guid")] == ["alpha", "beta", "gamma"]
    assert (workspace / "reports/report-20260114.html").exists()


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
def test_retry_then_success(failure):
    calls = []
    class Session:
        def request(self, *a, **kw):
            calls.append(kw)
            if len(calls) == 1 and failure == "timeout":
                raise requests.Timeout("synthetic")
            response = requests.Response()
            response.status_code = failure if len(calls) == 1 else 200
            return response
    response = request_with_retry(Session(), "GET", "https://example.invalid", logger=logging.getLogger(__name__), context="synthetic", timeout=3)
    assert response.status_code == 200
    assert len(calls) == 2
    assert calls == [{"timeout": 3}, {"timeout": 3}]


@pytest.mark.parametrize("status,attempts", [(401, 1), (503, 3)])
def test_http_error_propagates_after_expected_attempts(status, attempts):
    calls = []
    class Session:
        def request(self, *a, **kw):
            calls.append(1)
            response = requests.Response()
            response.status_code = status
            return response
    with pytest.raises(requests.HTTPError):
        request_with_retry(Session(), "GET", "https://example.invalid", logger=logging.getLogger(__name__), context="synthetic")
    assert len(calls) == attempts


def test_network_guard_rejects_unmocked_io():
    with pytest.raises(AssertionError, match="E0 forbids network"):
        requests.get("https://example.invalid")
    with pytest.raises(AssertionError, match="E0 forbids network"):
        socket.create_connection(("example.invalid", 443))
