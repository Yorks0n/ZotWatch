from datetime import timedelta

import requests

from src.fetch_new import CandidateFetcher
from .helpers import NOW
from .test_candidates import set_cache


def test_all_candidate_sources_unavailable_is_failed_not_successful_empty(workspace, settings, monkeypatch):
    fetcher = CandidateFetcher(settings, workspace)
    monkeypatch.setattr(fetcher, "_fetch_public_candidates", lambda *args: (_ for _ in ()).throw(requests.ConnectionError()))
    monkeypatch.setattr(fetcher, "_fetch_crossref_top_venues", lambda *args: [])
    outcome = fetcher.fetch_with_outcome()
    assert outcome.status == "failed"
    assert outcome.candidates == []
    assert outcome.request_complete is False


def test_stale_candidate_fallback_is_degraded(workspace, settings, candidates, monkeypatch):
    fetcher = CandidateFetcher(settings, workspace)
    set_cache(fetcher, candidates[:1], 13)
    monkeypatch.setattr(fetcher, "_fetch_public_candidates", lambda *args: (_ for _ in ()).throw(requests.ConnectionError()))
    monkeypatch.setattr(fetcher, "_fetch_crossref_top_venues", lambda *args: [])
    outcome = fetcher.fetch_with_outcome()
    assert outcome.status == "degraded"
    assert outcome.used_cache is True
    assert outcome.candidates[0].identifier == "alpha"
