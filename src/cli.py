from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from .build_profile import ProfileBuilder
from .dedupe import DedupeEngine
from .fetch_new import CandidateFetcher
from .ingest_zotero_api import ZoteroIngestor
from .logging_utils import setup_logging
from .models import RankedWork
from .push_to_zotero import ZoteroPusher
from .rss_writer import write_rss
from .score_rank import WorkRanker
from .settings import Settings, load_settings
from .storage import ProfileStorage
from .report_html import render_html

load_dotenv()  # Load default .env if present
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_SQLITE = BASE_DIR / "data" / "profile.sqlite"
RSS_PATH = BASE_DIR / "reports" / "feed.xml"


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="ZotWatcher CLI")
    parser.add_argument("command", choices=["profile", "watch"], help="Command to run")
    parser.add_argument("--base-dir", default=str(BASE_DIR), help="Repository base directory")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--full", action="store_true", help="Full rebuild (profile command)")
    parser.add_argument("--weekly", action="store_true", help="Alias for --full in profile command")
    parser.add_argument("--rss", action="store_true", help="Generate RSS feed (watch command)")
    parser.add_argument("--report", action="store_true", help="Generate HTML report (watch command)")
    parser.add_argument("--top", type=int, default=50, help="Number of top results to keep")
    parser.add_argument("--push", action="store_true", help="Push top items back to Zotero")

    args = parser.parse_args(argv)

    setup_logging(verbose=args.verbose)
    base_dir = Path(args.base_dir)
    load_dotenv(base_dir / ".env")
    settings = load_settings(base_dir)
    storage = ProfileStorage(base_dir / "data" / "profile.sqlite")

    if args.command == "profile":
        run_profile(base_dir, settings, storage, full=args.full or args.weekly)
    elif args.command == "watch":
        run_watch(base_dir, settings, storage, rss=args.rss, report=args.report, top=args.top, push=args.push)


def run_profile(base_dir: Path, settings: Settings, storage: ProfileStorage, *, full: bool) -> None:
    ingest = ZoteroIngestor(storage, settings)
    stats = ingest.run(full=full)
    logging.getLogger(__name__).info(
        "Ingest stats: fetched=%s updated=%s removed=%s", stats.fetched, stats.updated, stats.removed
    )
    builder = ProfileBuilder(base_dir, storage, settings)
    artifacts = builder.run()
    logging.getLogger(__name__).info(
        "Profile artifacts generated: sqlite=%s faiss=%s json=%s",
        artifacts.sqlite_path,
        artifacts.faiss_path,
        artifacts.profile_json_path,
    )


def run_watch(
    base_dir: Path,
    settings: Settings,
    storage: ProfileStorage,
    *,
    rss: bool,
    report: bool,
    top: int,
    push: bool,
) -> None:
    ingest = ZoteroIngestor(storage, settings)
    ingest.run(full=False)

    fetcher = CandidateFetcher(settings, base_dir)
    candidates = fetcher.fetch_all()

    dedupe = DedupeEngine(storage)
    filtered = dedupe.filter(candidates)

    ranker = WorkRanker(base_dir, settings)
    ranked = ranker.rank(filtered)

    ranked = _filter_recent(ranked, days=7)
    ranked = _limit_preprints(ranked, max_ratio=0.3)

    if top and len(ranked) > top:
        ranked = ranked[:top]

    if not ranked:
        logging.getLogger(__name__).info("No ranked results available")
        if rss:
            write_rss([], base_dir / "reports" / "feed.xml")
        if report:
            render_html([], base_dir / "reports" / "report-empty.html")
        return

    _log_top_results(ranked)

    if rss:
        write_rss(ranked, base_dir / "reports" / "feed.xml")
    if report:
        report_name = "report.html"
        if ranked[0].published:
            report_name = f"report-{ranked[0].published:%Y%m%d}.html"
        render_html(ranked, base_dir / "reports" / report_name)
    if push:
        ZoteroPusher(settings).push(ranked)


def _log_top_results(ranked: list[RankedWork]) -> None:
    logger = logging.getLogger(__name__)
    for idx, work in enumerate(ranked[:10], start=1):
        logger.info("%02d | %.3f | %s | %s", idx, work.score, work.label, work.title)


def _filter_recent(ranked: list[RankedWork], *, days: int) -> list[RankedWork]:
    if days <= 0:
        return ranked
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept = [work for work in ranked if work.published and work.published >= cutoff]
    removed = len(ranked) - len(kept)
    if removed > 0:
        logging.getLogger(__name__).info("Dropped %d items older than %d days", removed, days)
    return kept


def _limit_preprints(ranked: list[RankedWork], *, max_ratio: float) -> list[RankedWork]:
    if not ranked or max_ratio <= 0:
        return ranked
    preprint_sources = {"arxiv", "biorxiv", "medrxiv"}
    filtered: list[RankedWork] = []
    preprint_count = 0
    for work in ranked:
        source = work.source.lower()
        proposed_total = len(filtered) + 1
        if source in preprint_sources:
            proposed_preprints = preprint_count + 1
            if (proposed_preprints / proposed_total) > max_ratio:
                continue
            preprint_count = proposed_preprints
        filtered.append(work)
    removed = len(ranked) - len(filtered)
    if removed > 0:
        logging.getLogger(__name__).info("Preprint cap removed %d items to respect %.0f%% limit", removed, max_ratio * 100)
    return filtered


if __name__ == "__main__":
    main()
