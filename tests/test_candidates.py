import json
from datetime import timedelta

import pytest
import requests

from src import fetch_new
from .helpers import FIXTURES, NOW, Response, ids, read_json


def test_public_paging_mapping_and_preprint_loss(workspace, settings, monkeypatch):
    fetcher = fetch_new.CandidateFetcher(settings, workspace)
    pages = iter(read_json(FIXTURES / "public-pages.json"))
    calls = []
    def request(session, method, url, **kw):
        calls.append((method, url, kw))
        return Response(next(pages))
    monkeypatch.setattr(fetch_new, "request_with_retry", request)
    works = fetcher._fetch_public_candidates(NOW-timedelta(days=7), NOW, fetcher._enabled_public_sources())
    assert ids(works) == ["public-one", "public-two"]
    assert works[0].title == "Public &amp; synthetic"  # 1.x does not decode entities here.
    assert works[0].metrics == {"cited_by": 2.0}
    assert works[1].authors == ["Bo Example"]
    # BUG-F1: source-provided is_preprint is discarded by 1.x.
    assert "is_preprint" not in works[0].model_dump()
    assert "is_preprint" not in works[0].extra
    assert works[0].extra == {"candidate_type": "posted-content", "candidate_group": "preprint", "public_id": "public-id-1"}
    assert [call[2]["params"]["offset"] for call in calls] == [0, 2]
    assert calls[0][1] == "https://public.example.invalid/functions/v1/public-candidates-v1"
    assert calls[0][2]["headers"] == {"apikey": "synthetic-public-key"}
    assert calls[0][2]["params"] == dict(sources="crossref,arxiv,biorxiv", since="2026-01-08T12:00:00+00:00", until="2026-01-15T12:00:00+00:00", include_preprints="true", limit=200, offset=0)


@pytest.mark.parametrize("paging", [{}, {"next_offset": 0}, {"next_offset": 5}])
def test_empty_page_terminates(workspace, settings, monkeypatch, paging):
    calls = []
    monkeypatch.setattr(fetch_new, "request_with_retry", lambda *a, **kw: calls.append(kw) or Response({"data": [], "paging": paging}))
    fetcher = fetch_new.CandidateFetcher(settings, workspace)
    assert fetcher._fetch_public_candidates(NOW, NOW, ["crossref"]) == []
    assert len(calls) == 1
    assert calls[0]["params"]["include_preprints"] == "false"


def set_cache(fetcher, works, hours):
    fetcher._save_cache(works)
    data = read_json(fetcher.cache_path)
    data["fetched_at"] = (NOW-timedelta(hours=hours)).isoformat()
    fetcher.cache_path.write_text(json.dumps(data))


@pytest.mark.parametrize("age,refresh", [(11.9, False), (12, False), (12.001, True)])
def test_cache_boundary_and_config_not_part_of_key(workspace, settings, candidates, monkeypatch, age, refresh):
    fetcher = fetch_new.CandidateFetcher(settings, workspace)
    set_cache(fetcher, candidates[:1], age)
    settings.sources.crossref.enabled = False
    calls = []
    monkeypatch.setattr(fetcher, "_fetch_public_candidates", lambda *a: calls.append("public") or candidates[1:2])
    works = fetcher.fetch_all()
    assert ids(works) == (["beta"] if refresh else ["alpha"])
    assert calls == (["public"] if refresh else [])


@pytest.mark.parametrize("public", [True, False])
def test_source_dispatch_and_top_venue_supplement(workspace, settings, candidates, monkeypatch, public):
    settings.sources.public_api.enabled = public
    fetcher = fetch_new.CandidateFetcher(settings, workspace)
    calls = []
    for method in ("_fetch_public_candidates", "_fetch_openalex", "_fetch_crossref", "_fetch_arxiv", "_fetch_biorxiv", "_fetch_crossref_top_venues"):
        monkeypatch.setattr(fetcher, method, lambda *a, _name=method, **kw: calls.append(_name) or candidates[:1])
    works = fetcher.fetch_all()
    expected = ["_fetch_public_candidates", "_fetch_crossref_top_venues"] if public else ["_fetch_crossref", "_fetch_arxiv", "_fetch_biorxiv", "_fetch_crossref_top_venues"]
    assert calls == expected
    assert len(works) == len(expected)


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("supplement", [False, True])
def test_bug_public_failure_cache_and_topvenue_accounting(workspace, settings, candidates, monkeypatch, stale, supplement):
    fetcher = fetch_new.CandidateFetcher(settings, workspace)
    if stale:
        set_cache(fetcher, candidates[:1], 13)
    def fail(*a, **kw):
        raise requests.ConnectionError("synthetic failure")
    monkeypatch.setattr(fetcher, "_fetch_public_candidates", fail)
    # No automatic broad direct-source fallback is permitted by this baseline.
    for method in ("_fetch_openalex", "_fetch_crossref", "_fetch_arxiv", "_fetch_biorxiv"):
        monkeypatch.setattr(fetcher, method, lambda *a, **kw: pytest.fail("unexpected legacy fallback"))
    monkeypatch.setattr(fetcher, "_fetch_crossref_top_venues", lambda *a: candidates[1:2] if supplement else [])
    works = fetcher.fetch_all()
    # BUG-F2: successful supplement discarded when main source fails + nonempty stale cache.
    assert ids(works) == (["alpha"] if stale else (["beta"] if supplement else []))
    cached = read_json(fetcher.cache_path)
    # BUG-F3: no stale nonempty fallback => failure can be saved as fresh empty success.
    assert cached["fetched_at"] == ((NOW-timedelta(hours=13)) if stale else NOW).isoformat()


def test_corrupt_cache_and_top_venues(workspace, settings):
    data = workspace / "data"
    data.mkdir(exist_ok=True)
    (data / "profile.json").write_text(json.dumps({"top_venues": [{"venue": "Repeated"}, {"venue": "Repeated"}] + [{"venue": f"Venue {i}"} for i in range(25)]}))
    fetcher = fetch_new.CandidateFetcher(settings, workspace)
    assert len(fetcher.top_venues) == 20
    assert fetcher.top_venues[:2] == ["Repeated", "Venue 0"]
    fetcher.cache_path.write_text("invalid json")
    assert fetcher._load_cache() is None


def test_crossref_created_date_and_topvenue_request(workspace, settings, monkeypatch):
    data = workspace / "data"
    data.mkdir(exist_ok=True)
    (data / "profile.json").write_text('{"top_venues":[{"venue":"Example Journal"}]}')
    fetcher = fetch_new.CandidateFetcher(settings, workspace)
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return Response({"message": {"items": [{"DOI": "10.0000/new", "title": ["Synthetic title"], "created": {"date-time": "2026-01-10T00:00:00Z"}, "published": {"date-parts": [[2020, 1, 1]]}, "container-title": ["Example Journal"], "is-referenced-by-count": 3}]}})
    monkeypatch.setattr(fetch_new, "request_with_retry", request)
    direct = fetcher._fetch_crossref(NOW-timedelta(days=7))
    supplement = fetcher._fetch_crossref_top_venues(NOW-timedelta(days=7))
    assert direct[0].published.year == supplement[0].published.year == 2026
    assert calls[0]["params"]["rows"] == 200
    assert calls[1]["params"]["rows"] == 100
    assert "container-title:Example Journal" in calls[1]["params"]["filter"]
