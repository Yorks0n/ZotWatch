"""Explicit local evaluation command; no sync, recommendation dispatch or LLM calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .latent import LatentModelStore
from .latent_contract import LatentParameters


def evaluation_report(model, items):
    by_key = {item.key: item for item in items}
    eligible = len(model.embedding_keys)
    current_policy = "eligibility=abstract-required-v2" in model.clustering_runtime
    lines = ["# P5B3 private latent interest evaluation", "",
        f"Model revision: `{model.model_revision}`", f"Built at: {model.built_at}",
        f"Library revision at build: {model.library_revision}",
        f"Library items: {len(model.item_text_hashes)}; centers: {len(model.centers)}; "
        f"noise: {len(model.noise_keys)}/{eligible} ({len(model.noise_keys) / max(1, eligible):.1%}); "
        f"{'ineligible_for_interest_model (insufficient semantic text)' if current_policy else 'legacy empty-text exclusions'}: {len(model.excluded_keys)}.", "",
        "Stability is HDBSCAN density persistence (member-weighted after merge), not monthly repeatability.",
        "Counts and thresholds are exploratory; semantic quality requires review of representative papers.", "",
        "Representatives resolve against the current SQLite mirror; absent papers are shown as unavailable.", ""]
    def plain(value):
        return " ".join(value.split()).replace("\\", "\\\\").replace("`", "\\`").replace("<", "&lt;").replace(">", "&gt;").replace("[", "\\[").replace("]", "\\]")
    for center in sorted(model.centers, key=lambda center: (-center.member_count, center.interest_id)):
        lines.extend([f"## {center.interest_id}", "",
            f"Members: {center.member_count}; cohesion: {center.cohesion:.3f}; "
            f"density persistence: {center.stability:.3f}; change: {center.change}; "
            f"semantic refresh required: {center.semantic_refresh_required}.", ""])
        for key in center.representative_items:
            item = by_key.get(key)
            lines.append(f"- `{plain(key)}` — {plain(item.title) if item else '(unavailable)'}")
        lines.append("")
    return "\n".join(lines)


def main(arguments):
    from src.storage import ProfileStorage
    from src.computational_state import StateCoordinator
    from zotwatch.paths import RuntimePaths
    parser = argparse.ArgumentParser(prog="zotwatch interests")
    parser.add_argument("command", choices=["build", "inspect"])
    parser.add_argument("--workspace")
    parser.add_argument("--state-dir")
    parser.add_argument("--refresh", action="store_true", help="Explicitly rebuild regardless of monthly gate")
    parser.add_argument("--report", action="store_true", help="Write a private report with representative titles under state/latent")
    parser.add_argument("--min-cluster-size", type=int, default=10)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--merge-similarity", type=float, default=0.92)
    parser.add_argument("--selection", choices=["leaf", "eom"], default="leaf")
    args = parser.parse_args(arguments)
    paths = RuntimePaths.resolve(workspace=args.workspace, state_dir=args.state_dir)
    if not (paths.state / "profile.sqlite").is_file():
        print(json.dumps({"status": "failed", "reason": "SYNCED_LIBRARY_REQUIRED"}))
        return 4
    storage = ProfileStorage(paths.state / "profile.sqlite")
    try:
        with StateCoordinator(paths.state).acquire() as lease:
            store = LatentModelStore(paths.state)
            if args.command == "build":
                from src.vectorizer import TextVectorizer
                update = store.ensure(storage, TextVectorizer(), lease=lease, manual=args.refresh,
                    parameters=LatentParameters(min_cluster_size=args.min_cluster_size,
                        min_samples=args.min_samples, merge_similarity=args.merge_similarity, selection=args.selection))
                model, reason, rebuilt, encoded = update.model, update.reason, update.rebuilt, update.encoded_items
            else:
                model, _ = store.load()
                if model is None:
                    print(json.dumps({"status": "not_ready", "reason": "LATENT_MODEL_REQUIRED"}))
                    return 4
                reason, rebuilt, encoded = "inspect", False, 0
            snapshot = storage.read_profile_snapshot()
            if snapshot.library_identity_sha256 != model.library_identity_sha256:
                raise ValueError("Latent model belongs to another library")
            result = {"status": "succeeded", "model_revision": model.model_revision,
                "reason": reason, "rebuilt": rebuilt, "encoded_items": encoded,
                "library_items": len(model.item_text_hashes), "centers": len(model.centers),
                "noise_items": len(model.noise_keys), "excluded_items": len(model.excluded_keys)}
            result["interest_eligibility_policy"] = "abstract-required-v2" if "eligibility=abstract-required-v2" in model.clustering_runtime else "legacy-historical"
            result["ineligible_for_interest_model_items"] = len(model.excluded_keys) if result["interest_eligibility_policy"] == "abstract-required-v2" else None
            if args.report:
                report = store.root / f"evaluation-{model.model_revision}.md"
                report.write_text(evaluation_report(model, snapshot.items), encoding="utf-8")
                result["private_report"] = str(report)
            print(json.dumps(result))
            return 0
    except Exception:
        # No library titles, paths, credentials or upstream payloads in errors.
        print(json.dumps({"status": "failed", "reason": "LATENT_MODEL_FAILED"}))
        return 4
    finally:
        storage.close()
