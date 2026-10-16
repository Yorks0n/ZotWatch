from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import numpy as np

from src.cli import _filter_recent
from src import cli as engine
from src.dedupe import DedupeEngine
from src.models import CandidateWork
from zotwatch.interests.filter_diagnostic import FilterDiagnostic
from zotwatch.interests.ranking import rank
from .test_interests_p5b1 import profile
from zotwatch.interests.contract import Profile


def candidate(doi, **kw):
    return CandidateWork(source="crossref", identifier=doi, doi=doi, title=kw.pop("title", doi), **kw)


def test_actual_dedupe_branches_and_private_stage_accounting(tmp_path):
    works = [candidate("10.1093/bioinformatics/a"), candidate("10.1111/nph.b", title="Existing salt study"),
             candidate("10.1073/pnas.c", title="Library duplicate"), candidate("10.1111/tpj.d")]
    items = [SimpleNamespace(doi="10.1073/pnas.c", url=None, title="Existing salt study")]
    diag = FilterDiagnostic("a" * 32, Profile.model_validate(profile()))
    diag.collected(works)
    engine = DedupeEngine(SimpleNamespace(iter_items=lambda: iter(items)))
    baseline = engine.filter(works)
    observed = engine.filter(works, diagnostic=lambda w, r: diag.reject("library_dedupe", w, r))
    assert observed == baseline
    diag.stage("library_dedupe", works, observed)
    assert diag.data["candidates"][works[1].doi]["reason"] == "library_title_similarity"
    assert diag.data["candidates"][works[2].doi]["reason"] == "library_doi_duplicate"
    assert diag.data["stages"]["library_dedupe"]["eliminated"]["total"] == 2
    diag.finish(tmp_path, "succeeded")
    path = tmp_path / "runs" / ("filter-diagnostic-" + "a" * 32 + ".json")
    assert path.stat().st_mode & 0o777 == 0o600
    assert "description" not in json.loads(path.read_text())["topics"][0]


def test_topic_observer_uses_exact_existing_similarity_without_changing_rank():
    prof = Profile.model_validate(profile())
    works = [candidate("10.1111/nph.a"), candidate("10.1093/bioinformatics/b")]
    def encode(_, texts):
        return np.array([[1., 0.]]) if len(texts) == 1 else np.array([[.34, .94], [.36, .93]])
    baseline = rank(prof, works, None, encode=encode)
    diag = FilterDiagnostic("b" * 32, prof)
    diag.collected(works)
    observed = rank(prof, works, None, encode=encode, diagnostic=diag.topic)
    assert observed == baseline
    assert diag.data["candidates"][works[0].doi]["below_0_35"] is True
    assert diag.data["candidates"][works[1].doi]["below_0_35"] is False
    assert len(diag.data["ranking_topic_ids"]) == 1


def test_created_vs_publication_cutoff_observation_keeps_original_gate():
    now = engine.datetime.now(timezone.utc)
    work = candidate("10.1111/tpj.a", published=now - timedelta(days=8), extra={"source":"top_venue",
        "diagnostic_publication_dates":{"published":{"date-parts":[[now.year, now.month, now.day]]}}})
    diag = FilterDiagnostic("c" * 32, Profile.model_validate(profile()))
    diag.collected([work])
    baseline = _filter_recent([work], days=7)
    result = _filter_recent([work], days=7, diagnostic=lambda cutoff, before, after: diag.dates(cutoff))
    assert result == baseline == []
    comparison = diag.data["crossref_dates"][0]["comparison"]["published"]
    assert comparison["changes_midnight_verdict"] is True
    assert comparison["cutoff_day_ambiguous"] is False
