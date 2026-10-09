"""Automatic sync -> per-user lifecycle -> recall -> latent-auto ranking."""
import logging
import os
from uuid import uuid4
from .auto_results import AutoEvidence, AutoRunResult
from .auto_ranking import rank, select
from .center_recall_contract import digest
from .lifecycle import LifecycleStore, RunDeploymentEvidence, load_runtime
from .recall_integration import LatentRecallUnavailable, encoder_cache, work_key
from .runner import _storage
from zotwatch.metadata import Cache, enrich
from zotwatch.publication_dates import admission


def run(args, paths, effective):
    from src import cli as engine
    from src.computational_state import StateCoordinator, StateManager
    from zotwatch.runtime.preflight import preflight
    from .filter_diagnostic import FilterDiagnostic

    run_id, evidence, deployment, generation = uuid4().hex, None, None, None
    diagnostic = FilterDiagnostic(run_id, None) if os.getenv("ZOTWATCH_PRIVATE_FILTER_DIAGNOSTIC") == "1" else None
    repository_id = int(os.getenv("GITHUB_REPOSITORY_ID") or os.getenv("ZOTWATCH_WORKSPACE_REPOSITORY_ID", "0"))
    library = engine._library_identity(effective.settings)
    lifecycle = LifecycleStore(paths.state)
    cache = args.latent_encoder_cache or encoder_cache()

    def finish(status, reason, code=0, recommendations=None):
        result = AutoRunResult(run_id=run_id, command=args.command, status=status,
            reason=reason, exit_code=code, evidence=evidence, state_generation_id=generation,
            recommendations=recommendations or [])
        target = paths.state / "runs" / f"latent-auto-{run_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(result.model_dump_json() + "\n")
        temporary.replace(target)
        sidecar = RunDeploymentEvidence(run_id=run_id, deployment=deployment,
            deployment_sha256=digest(deployment.model_dump()) if deployment else None)
        sidecar.validate_result(result)
        (target.parent / f"latent-deployment-{run_id}.json").write_text(sidecar.model_dump_json() + "\n")
        if diagnostic:
            diagnostic.finish(paths.state, status)
        return result

    try:
        report = preflight(effective, verify_zotero=True)
        if not report.ready:
            return finish("not_ready", report.error_code)
        with _storage(paths.state) as storage:
            coordinator = StateCoordinator(paths.state)
            manager = StateManager(paths.state, coordinator=coordinator)
            with coordinator.acquire() as lease:
                engine.ZoteroIngestor(storage, effective.settings).run(full=args.full or args.weekly,
                    library_identity_sha256=library, lease=lease)
                try:
                    deployment, gate, encoded = lifecycle.ensure(storage, repository_id, library,
                        cache, manual=args.full, lease=lease)
                    deployment, runtime = load_runtime(lifecycle, repository_id, library, cache)
                except (OSError, ValueError) as exc:
                    return finish("not_ready", exc.reason if isinstance(exc, LatentRecallUnavailable) else "LATENT_LIFECYCLE_BUILD_FAILED")
                evidence = AutoEvidence(**runtime.metadata(), workspace_repository_id=repository_id,
                    library_identity_sha256=library, encoder_fingerprint=runtime.model.embedding_text_fingerprint,
                    formal_center_ids=list(runtime.index.ids))
                if diagnostic:
                    diagnostic.data.update(lifecycle_gate=gate, encoded_items=encoded,
                        formal_center_ids=list(runtime.index.ids), encoder_revision=runtime.model.encoder.model_revision)
                if args.command == "profile":
                    return finish("succeeded", "LATENT_MODEL_READY")
                vectorizer = engine.build_profile_module.TextVectorizer()
                handle, _ = engine._ensure_computational_state(paths.workspace, effective.settings, storage,
                    state_root=paths.state, manager=manager, vectorizer=vectorizer, lease=lease,
                    force=args.full or args.weekly)
                generation = handle.generation_id
                outcome = engine.CandidateFetcher(effective.settings, paths.workspace,
                    profile_summary=handle.profile, state_dir=paths.state).fetch_with_outcome()
                if outcome.status != "succeeded":
                    return finish("degraded" if outcome.status == "degraded" else "failed",
                        "CANDIDATE_PARTIAL" if outcome.status == "degraded" else "CANDIDATE_UNAVAILABLE",
                        5 if outcome.status == "degraded" else 4)
                try:
                    metadata = enrich(outcome.candidates, Cache(paths.state, repository_id))
                except (OSError, ValueError) as exc:
                    logging.getLogger(__name__).warning("Metadata cache unavailable: %s", type(exc).__name__)
                    metadata = {"status":"unavailable"}
                if diagnostic:
                    diagnostic.data["metadata_enrichment"] = metadata
                    inputs = diagnostic.collected(outcome.candidates)
                recalled, decisions = runtime.recall(outcome.candidates)
                if diagnostic:
                    diagnostic.data["recall_decisions"] = decisions
                    diagnostic.stage("center_recall", inputs, recalled)
                deduped = engine.DedupeEngine(storage).filter(recalled)
                if diagnostic:
                    diagnostic.stage("library_dedupe", recalled, deduped)
                recent, window = admission(deduped)
                if diagnostic:
                    diagnostic.data["publication_window"] = window
                    for candidate in deduped:
                        if work_key(candidate) in window["rejected"]:
                            diagnostic.reject("seven_day_admission", candidate, window["rejected"][work_key(candidate)])
                    diagnostic.stage("seven_day_admission", deduped, recent)
                rows = rank(recent, decisions)
                diversified = select(rows, limit=None)
                by_key = {work_key(c): c for c in recent}
                ordered = [by_key[r["work_key"]] for r in diversified]
                admitted = engine._limit_preprints(ordered, max_ratio=effective.max_preprint_ratio)
                allowed = {work_key(c) for c in admitted}
                final = [r for r in diversified if r["work_key"] in allowed][:20]
                if diagnostic:
                    diagnostic.stage("preprint_admission", ordered, admitted)
                    diagnostic.data["admitted_ranking"] = [r for r in rows if r["work_key"] in allowed]
                    diagnostic.stage("final_recommendations", admitted, [by_key[r["work_key"]] for r in final])
            return finish("succeeded", "LATENT_AUTO_READY", recommendations=final)
    except LatentRecallUnavailable as exc:
        return finish("not_ready", exc.reason)
    except Exception:
        logging.getLogger(__name__).exception("Latent auto run failed")
        return finish("failed", "LATENT_AUTO_RUN_FAILED", 4)
