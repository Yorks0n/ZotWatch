import json
import shutil
from datetime import timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from src.build_profile import ProfileBuilder
from src.faiss_store import FaissIndex
from src.report_html import render_html
from src.rss_writer import write_rss
from src.score_rank import WorkRanker, _compute_recency, _compute_metric, _journal_quality_score
from src.vectorizer import TextVectorizer
from .helpers import FIXTURES, GOLDENS, NOW, FixedVectors, assert_json, ids, read_json


def build_and_rank(workspace, library, settings, candidates):
    settings.scoring.whitelist_authors = ["ada example"]
    settings.scoring.whitelist_venues = ["EXAMPLE JOURNAL"]
    artifacts = ProfileBuilder(workspace, library, settings, FixedVectors()).run()
    shutil.copyfile(FIXTURES / "journal_metrics.csv", workspace / "data/journal_metrics.csv")
    ranked = WorkRanker(workspace, settings, FixedVectors()).rank(candidates)
    return artifacts, ranked


def test_profile_and_rank_golden(workspace, library, settings, candidates):
    artifacts, ranked = build_and_rank(workspace, library, settings, candidates)
    assert_json(read_json(Path(artifacts.profile_json_path)), read_json(GOLDENS / "profile.json"))
    assert_json([work.model_dump(mode="json") for work in ranked], read_json(GOLDENS / "ranking.json"))
    assert ids(ranked).index("gamma") < ids(ranked).index("delta")  # Stable tie.
    assert {work.label for work in ranked} == {"must_read", "consider", "ignore"}
    assert len(library.fetch_all_embeddings()) == 2
    assert library.fetch_items_without_embedding() == []
    loaded = FaissIndex.load(artifacts.faiss_path)
    distances, neighbors = loaded.search(np.array([0.6, 0.8, 0]), top_k=2)
    np.testing.assert_allclose(distances, [[0.8, 0.6]], rtol=0, atol=1e-6)
    assert neighbors.tolist() == [[1, 0]]


@pytest.mark.parametrize("empty", [False, True])
def test_output_goldens(workspace, library, settings, candidates, empty):
    _, ranked = build_and_rank(workspace, library, settings, candidates)
    if empty:
        ranked = []
    stem = "empty" if empty else "ranked"
    rss = write_rss(ranked, workspace / "feed.xml")
    report = render_html(ranked, workspace / "report.html")
    # Fixed clock permits full comparisons, including whitespace/escaping/dates.
    assert rss.read_bytes() == (GOLDENS / f"{stem}.xml").read_bytes()
    assert report.read_text() == (GOLDENS / f"{stem}.html").read_text()
    parsed = ET.parse(rss)
    assert [node.text for node in parsed.findall("channel/item/guid")] == ids(ranked)
    assert "<script>" not in report.read_text()
    if not empty:
        assert "&lt;script&gt;" in report.read_text()
        assert parsed.findtext("channel/item/title") == "Alpha <cells> & evidence"
        undated = next(node for node in parsed.findall("channel/item") if node.findtext("guid") == "eta")
        assert undated.findtext("pubDate") == "Thu, 15 Jan 2026 12:00:00 +0000"


@pytest.mark.parametrize("age,expected", [(None, 0), (-2, 1), (3, 1), (3.9, 1), (4, .7), (7, .7), (8, .4), (30, .4), (31, .1)])
def test_recency_boundaries(settings, age, expected):
    date = NOW - timedelta(days=age) if age is not None else None
    assert _compute_recency(date, settings) == expected


def test_missing_metrics_and_sjr(candidates):
    assert _compute_metric(candidates[1]) == (0, 0)
    assert _compute_metric(candidates[5]) == (pytest.approx(np.log(2)), 0)
    assert _journal_quality_score(None, {}) == (1, None)
    assert _journal_quality_score("unknown", {}) == (1, None)
    assert _journal_quality_score(" Low ", {"low": .1}) == (1, .1)


def test_empty_profile_and_missing_index(workspace, storage, settings):
    with pytest.raises(RuntimeError, match="No items"):
        ProfileBuilder(workspace, storage, settings, FixedVectors()).run()
    with pytest.raises(RuntimeError):
        WorkRanker(workspace, settings, FixedVectors())


def test_vectorizer_normalizes_without_loading_remote_model(monkeypatch):
    class Model:
        def encode(self, texts, show_progress_bar):
            assert texts == ["synthetic", "zero"]
            assert show_progress_bar is False
            return [[3, 4, 0], [0, 0, 0]]
    vectorizer = TextVectorizer()
    vectorizer._model = Model()
    actual = vectorizer.encode(iter(["synthetic", "zero"]))
    np.testing.assert_allclose(actual, [[.6, .8, 0], [0, 0, 0]], rtol=0, atol=1e-6)
    assert actual.dtype == np.float32
    from src import vectorizer as module
    monkeypatch.setattr(module, "SentenceTransformer", None)
    with pytest.raises(RuntimeError, match="not installed"):
        TextVectorizer().load()


def test_faiss_shape_and_empty_index(workspace):
    with pytest.raises(ValueError, match="2D"):
        FaissIndex.from_vectors(np.array([1, 0], dtype="float32"))
    index, order = FaissIndex.from_vectors(np.empty((0, 3), dtype="float32"))
    assert order.tolist() == []
    path = workspace / "empty.index"
    index.save(path)
    with pytest.raises(ValueError, match="empty"):
        FaissIndex.load(path)
