import csv
import shutil

from src.build_profile import ProfileBuilder
from src.score_rank import WorkRanker
from zotwatch.resources import journal_metrics_path
from .helpers import ROOT, FixedVectors, assert_json


def test_bundled_metrics_are_original_bytes_and_scores(workspace, library, settings, candidates):
    original = ROOT / "data/journal_metrics.csv"
    ProfileBuilder(workspace, library, settings, FixedVectors()).run()
    local = workspace / "data/journal_metrics.csv"
    shutil.copyfile(original, local)
    first = next(csv.DictReader(original.open(encoding="utf-8")))
    candidates[0].venue = first["title"]
    legacy = WorkRanker(workspace, settings, FixedVectors())
    local.unlink()
    with journal_metrics_path("bundled", workspace) as path:
        assert path.read_bytes() == original.read_bytes()
        bundled = WorkRanker(workspace, settings, FixedVectors(), metrics_path=path)
    assert bundled.journal_metrics == legacy.journal_metrics
    assert bundled.journal_metrics
    assert_json([w.model_dump(mode="json") for w in bundled.rank(candidates)],
                [w.model_dump(mode="json") for w in legacy.rank(candidates)])


def test_external_state_does_not_change_legacy_metrics_location(workspace, library, settings):
    state = workspace / "external-state"
    ProfileBuilder(workspace, library, settings, FixedVectors(), state_dir=state).run()
    (state / "journal_metrics.csv").write_text("title,sjr\nWrong location,99\n")
    ranker = WorkRanker(workspace, settings, FixedVectors(), state_dir=state)
    assert ranker.journal_metrics == {}  # Missing legacy CSV must not enable bundled SJR.
    (workspace / "data/journal_metrics.csv").write_text("title,sjr\nLegacy,3\n")
    ranker = WorkRanker(workspace, settings, FixedVectors(), state_dir=state)
    assert ranker.journal_metrics == {"legacy": 3}


def test_resource_selection_is_explicit(workspace):
    with journal_metrics_path("legacy", workspace) as path:
        assert path is None
    with journal_metrics_path("custom/metrics.csv", workspace) as path:
        assert path == workspace / "custom/metrics.csv"
    absolute = workspace / "metrics.csv"
    with journal_metrics_path(str(absolute), workspace) as path:
        assert path == absolute
