from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager

from .contract import InterestError
from .git import read_github, read_local, readiness
from .ranking import rank, semantic_input_hash
from .results import Evidence, TopicRunResult


def load_snapshot(workspace):
    if os.getenv("GITHUB_ACTIONS") == "true":
        return read_github(os.environ.get("GITHUB_REPOSITORY", ""), int(os.environ.get("GITHUB_REPOSITORY_ID", "0")), os.environ.get("GITHUB_TOKEN", ""))
    return read_local(Path(workspace))


def run(args, paths, effective, *, snapshot_loader=load_snapshot, ranker=rank):
    from src import cli as engine
    from src.computational_state import StateCoordinator, StateManager, descriptor_for_vectorizer
    from zotwatch.runtime.preflight import preflight

    run_id = uuid4().hex
    evidence, generation = None, None

    def finish(status, reason, code=0, recommendations=None):
        result = TopicRunResult(run_id=run_id, command=args.command, status=status,
                                reason=reason, exit_code=code, evidence=evidence,
                                state_generation_id=generation, recommendations=recommendations or [])
        target = paths.state / "runs" / f"topic-{run_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(result.model_dump_json() + "\n", encoding="utf-8")
        temporary.replace(target)
        return result

    try:
        if effective.publish_requested:
            raise InterestError("INTEREST_PUBLICATION_UNSUPPORTED")
        value = snapshot_loader(paths.workspace)
        if value is not None:
            evidence = Evidence(feedback_commit_sha=value.feedback_commit_sha,
                                profile_blob_sha=value.profile_blob_sha,
                                semantic_input_sha256=semantic_input_hash(value.profile))
        if args.command == "watch":
            repository_id = int(os.getenv("GITHUB_REPOSITORY_ID") or os.getenv("ZOTWATCH_WORKSPACE_REPOSITORY_ID", "0"))
            state = readiness(value, repository_id, effective.settings.zotero.api.user_id)
            if state != "ready":
                return finish(state, "INTEREST_" + state.upper())
        report = preflight(effective)
        if not report.ready:
            return finish("failed", report.error_code, 3)
        with _storage(paths.state) as storage:
            coordinator = StateCoordinator(paths.state)
            manager = StateManager(paths.state, coordinator=coordinator)
            vectorizer = engine.build_profile_module.TextVectorizer()
            with coordinator.acquire() as lease:
                engine.ZoteroIngestor(storage, effective.settings).run(
                    full=args.full or args.weekly,
                    library_identity_sha256=engine._library_identity(effective.settings), lease=lease)
                handle, _ = engine._ensure_computational_state(
                    paths.workspace, effective.settings, storage, state_root=paths.state,
                    manager=manager, vectorizer=vectorizer, lease=lease, force=args.full or args.weekly)
                generation = handle.generation_id
                if args.command == "profile":
                    return finish("succeeded", "PROFILE_BUILT")
                descriptor = descriptor_for_vectorizer(vectorizer)
                evidence = evidence.model_copy(update={"model_fingerprint": descriptor.hard_fingerprint_sha256})
                outcome = engine.CandidateFetcher(effective.settings, paths.workspace,
                    profile_summary=handle.profile, state_dir=paths.state).fetch_with_outcome()
                if outcome.status == "failed":
                    raise InterestError("CANDIDATE_UNAVAILABLE")
                if outcome.status == "degraded":
                    return finish("degraded", "CANDIDATE_PARTIAL", 5)
                candidates = engine.DedupeEngine(storage).filter(outcome.candidates)
                # Existing seven-day and preprint admission rules remain, without metadata scoring.
                candidates = engine._filter_recent(candidates, days=7)
                ranked = ranker(value.profile, candidates, vectorizer)
                by_key = {c.doi or f"{c.source}:{c.identifier}": c for c in candidates}
                ordered = [by_key[item["work_key"]] for item in ranked]
                allowed = {c.doi or f"{c.source}:{c.identifier}" for c in engine._limit_preprints(ordered, max_ratio=effective.max_preprint_ratio)}
                ranked = [r for r in ranked if r["work_key"] in allowed][:effective.top_n]
            return finish("succeeded", "INTEREST_READY", recommendations=ranked)
    except InterestError as exc:
        return finish("failed", exc.code, 4)
    except Exception:
        return finish("failed", "INTEREST_RUN_FAILED", 4)


@contextmanager
def _storage(state):
    from src.storage import ProfileStorage
    storage = ProfileStorage(state / "profile.sqlite")
    try:
        yield storage
    finally:
        storage.close()
