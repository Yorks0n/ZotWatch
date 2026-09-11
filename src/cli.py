from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from zotwatch.paths import RuntimePaths
from zotwatch.resources import journal_metrics_path

from . import build_profile as build_profile_module
from .build_profile import ProfileBuilder
from .computational_state import (
    ComputationalStateError,
    StateCoordinator,
    StateFutureSchemaError,
    StateHandle,
    StateLease,
    StateManager,
    descriptor_for_vectorizer,
    expectation_from_snapshot,
    pseudonymous_library_identity,
)
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

def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="ZotWatcher CLI")
    parser.add_argument("command", choices=["profile", "watch"], help="Command to run")
    parser.add_argument("--workspace", help="Personal workspace (default: current directory)")
    parser.add_argument("--base-dir", help="Compatibility alias for --workspace")
    parser.add_argument("--state-dir", help="State directory (default: workspace/data)")
    parser.add_argument("--reports-dir", help="Reports directory (default: workspace/reports)")
    parser.add_argument("--journal-metrics", default="legacy",
                        help="legacy (default), bundled, or a workspace-relative/absolute CSV path")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--full", action="store_true", help="Full rebuild (profile command)")
    parser.add_argument("--weekly", action="store_true", help="Alias for --full in profile command")
    parser.add_argument("--rss", action="store_true", help="Generate RSS feed (watch command)")
    parser.add_argument("--report", action="store_true", help="Generate HTML report (watch command)")
    parser.add_argument("--top", type=int, default=50, help="Number of top results to keep")
    parser.add_argument("--push", action="store_true", help="Push top items back to Zotero")

    args = parser.parse_args(argv)

    setup_logging(verbose=args.verbose)
    try:
        paths = RuntimePaths.resolve(workspace=args.workspace, base_dir=args.base_dir,
                                     state_dir=args.state_dir, reports_dir=args.reports_dir)
    except ValueError as exc:
        parser.error(str(exc))
    base_dir = paths.workspace
    load_dotenv(base_dir / ".env")
    settings = load_settings(base_dir)
    storage = ProfileStorage(paths.state / "profile.sqlite")
    # Default calls keep the legacy API; overrides are explicit keyword arguments.
    path_options = {"state_dir": paths.state} if args.state_dir is not None else {}
    try:
        if args.command == "profile":
            run_profile(base_dir, settings, storage, full=args.full or args.weekly, **path_options)
        elif args.command == "watch":
            if args.reports_dir is not None:
                path_options["reports_dir"] = paths.reports
            if args.journal_metrics != "legacy":
                path_options["journal_metrics"] = args.journal_metrics
            run_watch(base_dir, settings, storage, rss=args.rss, report=args.report,
                      top=args.top, push=args.push, **path_options)
    finally:
        storage.close()


def run_profile(base_dir: Path, settings: Settings, storage: ProfileStorage, *, full: bool,
                state_dir: Path | None = None) -> None:
    state_root = Path(state_dir) if state_dir is not None else storage.path.parent
    coordinator = StateCoordinator(state_root)
    manager = StateManager(state_root, coordinator=coordinator)
    vectorizer = build_profile_module.TextVectorizer()
    with coordinator.acquire() as lease:
        ingest = ZoteroIngestor(storage, settings)
        stats = ingest.run(
            full=full,
            library_identity_sha256=_library_identity(settings),
            lease=lease,
        )
        logging.getLogger(__name__).info(
            "Ingest stats: fetched=%s updated=%s removed=%s",
            stats.fetched,
            stats.updated,
            stats.removed,
        )
        handle, artifacts = _ensure_computational_state(
            base_dir,
            settings,
            storage,
            state_root=state_root,
            manager=manager,
            vectorizer=vectorizer,
            lease=lease,
            force=full,
        )
    logging.getLogger(__name__).info(
        "Profile artifacts ready: generation=%s sqlite=%s faiss=%s json=%s",
        handle.generation_id,
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
    state_dir: Path | None = None,
    reports_dir: Path | None = None,
    journal_metrics: str = "legacy",
) -> None:
    state_root = Path(state_dir) if state_dir is not None else storage.path.parent
    coordinator = StateCoordinator(state_root)
    manager = StateManager(state_root, coordinator=coordinator)
    vectorizer = build_profile_module.TextVectorizer()
    path_options = {"state_dir": state_root}
    output_dir = Path(reports_dir) if reports_dir is not None else base_dir / "reports"
    with coordinator.acquire() as lease:
        ingest = ZoteroIngestor(storage, settings)
        ingest.run(
            full=False,
            library_identity_sha256=_library_identity(settings),
            lease=lease,
        )
        state_handle, _ = _ensure_computational_state(
            base_dir,
            settings,
            storage,
            state_root=state_root,
            manager=manager,
            vectorizer=vectorizer,
            lease=lease,
            force=False,
        )

        fetcher = CandidateFetcher(
            settings,
            base_dir,
            profile_summary=state_handle.profile,
            **path_options,
        )
        candidates = fetcher.fetch_all()
        dedupe = DedupeEngine(storage)
        filtered = dedupe.filter(candidates)

        with journal_metrics_path(journal_metrics, base_dir) as metrics_path:
            ranker_options = dict(path_options)
            if metrics_path is not None:
                ranker_options["metrics_path"] = metrics_path
            ranker = WorkRanker(
                base_dir,
                settings,
                vectorizer,
                state_handle=state_handle,
                **ranker_options,
            )
        ranked = ranker.rank(filtered)

    ranked = _filter_recent(ranked, days=7)
    ranked = _limit_preprints(ranked, max_ratio=0.3)

    if top and len(ranked) > top:
        ranked = ranked[:top]

    if not ranked:
        logging.getLogger(__name__).info("No ranked results available")
        if rss:
            write_rss([], output_dir / "feed.xml")
        if report:
            render_html([], output_dir / "report-empty.html")
        return

    _log_top_results(ranked)

    if rss:
        write_rss(ranked, output_dir / "feed.xml")
    if report:
        report_name = "report.html"
        if ranked[0].published:
            report_name = f"report-{ranked[0].published:%Y%m%d}.html"
        render_html(ranked, output_dir / report_name)
    if push:
        ZoteroPusher(settings).push(ranked)


def _library_identity(settings: Settings) -> str:
    return pseudonymous_library_identity("user", settings.zotero.api.user_id)


def _ensure_computational_state(
    base_dir: Path,
    settings: Settings,
    storage: ProfileStorage,
    *,
    state_root: Path,
    manager: StateManager,
    vectorizer,
    lease: StateLease,
    force: bool,
):
    descriptor = descriptor_for_vectorizer(vectorizer)
    snapshot = storage.read_profile_snapshot()
    expectation = expectation_from_snapshot(snapshot, descriptor)
    if not force:
        try:
            handle = manager.load_current(expectation)
            return handle, _artifacts_from_handle(storage, handle)
        except StateFutureSchemaError:
            raise
        except ComputationalStateError as exc:
            logging.getLogger(__name__).info(
                "Computational state requires rebuild: %s", exc
            )

    builder = ProfileBuilder(
        base_dir,
        storage,
        settings,
        vectorizer,
        state_dir=state_root,
        state_manager=manager,
    )
    artifacts = builder.run(lease=lease)
    current_snapshot = storage.read_profile_snapshot()
    current_expectation = expectation_from_snapshot(current_snapshot, descriptor)
    handle = manager.load_current(current_expectation)
    return handle, artifacts


def _artifacts_from_handle(storage: ProfileStorage, handle: StateHandle):
    from .models import ProfileArtifacts

    return ProfileArtifacts(
        sqlite_path=str(storage.path),
        faiss_path=str(handle.index_path),
        profile_json_path=str(handle.profile_path),
        manifest_path=str(handle.manifest_path),
        embeddings_path=str(handle.embeddings_path),
        generation_id=handle.generation_id,
    )


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
