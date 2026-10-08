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
    per_user = getattr(args, "latent_lifecycle", None) == "per-user-v1"
    deployment = None
    repository_id = 0
    if per_user:
        repository_id = int(os.getenv("GITHUB_REPOSITORY_ID") or os.getenv("ZOTWATCH_WORKSPACE_REPOSITORY_ID", "0"))
        from .lifecycle import LifecycleStore, load_runtime, RunDeploymentEvidence
        lifecycle = LifecycleStore(paths.state)
    integrated = getattr(args, "candidate_policy", "confirmed-topic-candidates-v1") == "center-recall-v1"
    result_type, evidence_type = TopicRunResult, Evidence
    if integrated:
        from .integration_results import IntegrationRunResult, IntegrationEvidence
        from .recall_integration import LatentRecallRuntime, LatentRecallUnavailable, encoder_cache, attach_recall
        result_type, evidence_type = IntegrationRunResult, IntegrationEvidence

    def finish(status, reason, code=0, recommendations=None):
        result = result_type(run_id=run_id, command=args.command, status=status,
                                reason=reason, exit_code=code, evidence=evidence,
                                state_generation_id=generation, recommendations=recommendations or [])
        target = paths.state / "runs" / f"topic-{run_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(result.model_dump_json() + "\n", encoding="utf-8")
        temporary.replace(target)
        if per_user:
            from .center_recall_contract import digest
            sidecar = RunDeploymentEvidence(run_id=run_id, deployment=deployment,
                deployment_sha256=digest(deployment.model_dump()) if deployment else None)
            sidecar.validate_result(result)
            (target.parent / f"latent-deployment-{run_id}.json").write_text(sidecar.model_dump_json() + "\n")
        return result

    try:
        if effective.publish_requested:
            raise InterestError("INTEREST_PUBLICATION_UNSUPPORTED")
        value = snapshot_loader(paths.workspace)
        if value is not None:
            evidence = evidence_type(feedback_commit_sha=value.feedback_commit_sha,
                                profile_blob_sha=value.profile_blob_sha,
                                semantic_input_sha256=semantic_input_hash(value.profile))
        if args.command == "watch":
            repository_id = int(os.getenv("GITHUB_REPOSITORY_ID") or os.getenv("ZOTWATCH_WORKSPACE_REPOSITORY_ID", "0"))
            state = readiness(value, repository_id, effective.settings.zotero.api.user_id)
            if state != "ready":
                return finish(state, "INTEREST_" + state.upper())
            if integrated:
                try:
                    model_path = getattr(args, "latent_recall_model", None) or paths.state / "latent/recall/center-recall-v1/model.json"
                    cache_path = getattr(args, "latent_encoder_cache", None) or encoder_cache()
                    if per_user:
                        deployment, recall_runtime = load_runtime(lifecycle, repository_id,
                            engine._library_identity(effective.settings), cache_path)
                    if getattr(args, "latent_staging", False):
                        from .staging_runtime import validate_model
                        validate_model(model_path)
                    if not per_user:
                        recall_runtime = LatentRecallRuntime(model_path, cache_path)
                except LatentRecallUnavailable as exc:
                    return finish("not_ready", exc.reason)
                evidence = evidence_type.model_validate({**evidence.model_dump(), "latent_recall": recall_runtime.metadata()})
                if getattr(args, "latent_staging", False):
                    from .staging_runtime import validate_runtime
                    try:
                        validate_runtime(model_path, cache_path)
                    except LatentRecallUnavailable as exc:
                        return finish("not_ready", exc.reason)
        report = preflight(effective, verify_zotero=True) if per_user else preflight(effective)
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
                if per_user:
                    from .recall_integration import LatentRecallUnavailable, encoder_cache
                    cache_path = getattr(args, "latent_encoder_cache", None) or encoder_cache()
                    try:
                        deployment, _, _ = lifecycle.ensure(storage, repository_id, engine._library_identity(effective.settings),
                            cache_path, manual=args.full, lease=lease)
                        if integrated:
                            deployment, recall_runtime = load_runtime(lifecycle, repository_id,
                                engine._library_identity(effective.settings), cache_path)
                            evidence = evidence_type.model_validate({**evidence.model_dump(), "latent_recall": recall_runtime.metadata()})
                    except (OSError, ValueError) as exc:
                        reason = exc.reason if isinstance(exc, LatentRecallUnavailable) else "LATENT_LIFECYCLE_BUILD_FAILED"
                        return finish("not_ready", reason)
                handle, _ = engine._ensure_computational_state(
                    paths.workspace, effective.settings, storage, state_root=paths.state,
                    manager=manager, vectorizer=vectorizer, lease=lease, force=args.full or args.weekly)
                generation = handle.generation_id
                from .suggestions import write_projection
                builder = engine.build_profile_module.ProfileBuilder(paths.workspace, storage,
                    effective.settings, vectorizer=vectorizer, state_dir=paths.state)
                write_projection(paths.state, run_id, builder.suggest_interests())
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
                collected = outcome.candidates
                if integrated:
                    try:
                        collected, recall_decisions = recall_runtime.recall(collected)
                    except LatentRecallUnavailable as exc:
                        return finish("not_ready", exc.reason)
                    evidence = evidence_type.model_validate({**evidence.model_dump(), "recall_decisions": recall_decisions})
                candidates = engine.DedupeEngine(storage).filter(collected)
                # Existing seven-day and preprint admission rules remain, without metadata scoring.
                candidates = engine._filter_recent(candidates, days=7)
                ranked = ranker(value.profile, candidates, vectorizer)
                by_key = {c.doi or f"{c.source}:{c.identifier}": c for c in candidates}
                ordered = [by_key[item["work_key"]] for item in ranked]
                allowed = {c.doi or f"{c.source}:{c.identifier}" for c in engine._limit_preprints(ordered, max_ratio=effective.max_preprint_ratio)}
                ranked = [r for r in ranked if r["work_key"] in allowed][:effective.top_n]
                if integrated:
                    ranked = attach_recall(ranked, recall_decisions, value.profile_blob_sha)
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
