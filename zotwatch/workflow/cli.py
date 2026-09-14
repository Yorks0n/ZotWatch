from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

EXPECTED_ENGINE_REPOSITORY = "Yorks0n/ZotWatch"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m zotwatch.workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    identity = subparsers.add_parser("identity")
    identity.add_argument("--workflow-repository", required=True)
    identity.add_argument("--workflow-sha", required=True)
    identity.add_argument("--engine", required=True)
    identity.add_argument("--github-output", action="store_true")

    inspect = subparsers.add_parser("inspect-config")
    inspect.add_argument("--workspace", required=True)
    inspect.add_argument("--config-path", required=True)
    inspect.add_argument("--expected-config-sha", default="")
    inspect.add_argument("--mode", choices=("run", "profile", "validate"), required=True)
    inspect.add_argument("--full-rebuild", choices=("true", "false"), required=True)
    inspect.add_argument("--github-output", action="store_true")

    restore = subparsers.add_parser("restore")
    _github_context_arguments(restore)
    restore.add_argument("--token", default=None)
    restore.add_argument("--engine-repository", default=EXPECTED_ENGINE_REPOSITORY)
    restore.add_argument("--workspace", required=True)
    restore.add_argument("--state", required=True)
    restore.add_argument("--staging", required=True)
    restore.add_argument("--context-output", required=True)

    result = subparsers.add_parser("result")
    result.add_argument("--machine-result", required=True)
    result.add_argument("--process-exit-code", required=True, type=int)
    result.add_argument("--state", required=True)
    result.add_argument("--reports", required=True)
    result.add_argument("--publishable", required=True)
    result.add_argument("--private", required=True)
    result.add_argument("--summary-output", required=True)
    result.add_argument("--github-output", action="store_true")

    checkpoint = subparsers.add_parser("export-checkpoint")
    checkpoint.add_argument("--context", required=True)
    checkpoint.add_argument("--engine-repository", default=EXPECTED_ENGINE_REPOSITORY)
    checkpoint.add_argument("--engine-sha", required=True)
    checkpoint.add_argument("--engine-run-id", required=True)
    checkpoint.add_argument("--config-fingerprint", required=True)
    checkpoint.add_argument("--state", required=True)
    checkpoint.add_argument("--destination", required=True)

    seal = subparsers.add_parser("seal-result")
    seal.add_argument("--private", required=True)
    seal.add_argument("--engine-sha", required=True)
    seal.add_argument("--repository", required=True)
    seal.add_argument("--repository-id", required=True, type=int)
    seal.add_argument("--caller-run-id", required=True, type=int)
    seal.add_argument("--run-id", required=True)
    seal.add_argument("--status", required=True)
    seal.add_argument("--publish-requested", choices=("true", "false"), required=True)

    pages = subparsers.add_parser("pages")
    pages.add_argument("--private", required=True)
    pages.add_argument("--reports", required=True)
    pages.add_argument("--destination", required=True)
    pages.add_argument("--engine-sha", required=True)
    pages.add_argument("--repository", required=True)
    pages.add_argument("--repository-id", required=True, type=int)
    pages.add_argument("--caller-run-id", required=True, type=int)
    pages.add_argument("--run-id", required=True)
    return parser


def _github_context_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-id", required=True, type=int)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--event", required=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "identity":
            from .identity import verify_workflow_identity

            value = verify_workflow_identity(
                expected_repository=EXPECTED_ENGINE_REPOSITORY,
                workflow_repository=args.workflow_repository,
                workflow_sha=args.workflow_sha,
                engine_path=args.engine,
            )
            payload = {"engine_sha": value.engine_sha}
            _emit(payload)
            if args.github_output:
                _write_github_output(payload)
        elif args.command == "inspect-config":
            _inspect_config(args)
        elif args.command == "restore":
            _restore(args)
        elif args.command == "result":
            _result(args)
        elif args.command == "export-checkpoint":
            _export(args)
        elif args.command == "seal-result":
            from .results import seal_private_result

            seal_private_result(
                args.private,
                engine_repository=EXPECTED_ENGINE_REPOSITORY,
                engine_sha=args.engine_sha,
                workspace_repository=args.repository,
                workspace_repository_id=args.repository_id,
                caller_run_id=args.caller_run_id,
                run_id=args.run_id,
                result_status=args.status,
                publish_requested=args.publish_requested == "true",
            )
        else:
            from .results import materialize_pages_payload

            materialize_pages_payload(
                args.private,
                args.reports,
                args.destination,
                expected_engine_repository=EXPECTED_ENGINE_REPOSITORY,
                expected_engine_sha=args.engine_sha,
                expected_workspace_repository=args.repository,
                expected_workspace_repository_id=args.repository_id,
                expected_caller_run_id=args.caller_run_id,
                expected_run_id=args.run_id,
            )
    except Exception as exc:
        print(f"P2_WORKFLOW_CONTRACT_ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


def _inspect_config(args) -> None:
    from zotwatch.runtime import load_effective_runtime

    workspace = Path(args.workspace).resolve()
    if args.config_path != "zotwatch.yaml":
        raise ValueError("config-path must be exactly zotwatch.yaml in P2 v1")
    config_path = workspace / args.config_path
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("zotwatch.yaml must be a regular non-symlink file")
    if args.mode == "validate" and args.full_rebuild == "true":
        raise ValueError("validate mode cannot request a full rebuild")
    if args.expected_config_sha:
        if not re.fullmatch(r"[0-9a-f]{40}", args.expected_config_sha):
            raise ValueError("expected-config-sha must be a full Git object SHA")
        actual = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD:zotwatch.yaml"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        if actual != args.expected_config_sha:
            raise ValueError("CONFIG_REVISION_MISMATCH")
    effective = load_effective_runtime(workspace)
    payload = {
        "config_fingerprint_sha256": effective.config_fingerprint_sha256,
        "publish_requested": str(effective.publish_requested).lower(),
    }
    _emit(payload)
    if args.github_output:
        _write_github_output(payload)


def _restore(args) -> None:
    from zotwatch.runtime import load_effective_runtime
    from .artifacts import GitHubArtifactSource, restore_prior_checkpoint
    from .checkpoint import CheckpointExpectation

    effective = load_effective_runtime(Path(args.workspace))
    token = args.token or os.environ.get("GITHUB_TOKEN", "")
    source = GitHubArtifactSource(token)
    context = source.discover_current_context(
        repository=args.repository,
        repository_id=args.repository_id,
        run_id=args.run_id,
        ref=args.ref,
        event=args.event,
    )

    def expectation(run):
        return CheckpointExpectation(
            engine_repository=args.engine_repository,
            workspace_repository=context.repository,
            workspace_repository_id=context.repository_id,
            caller_workflow_id=context.workflow_id,
            caller_workflow_path=context.workflow_path,
            caller_event=run.event,
            caller_run_id=run.run_id,
            caller_ref=context.ref,
            config_fingerprint_sha256=effective.config_fingerprint_sha256,
        )

    outcome = restore_prior_checkpoint(
        source,
        context,
        args.state,
        args.staging,
        expectation_factory=expectation,
    )
    context_payload = {
        **context.__dict__,
        "run_attempt": args.run_attempt,
        "config_fingerprint_sha256": effective.config_fingerprint_sha256,
    }
    Path(args.context_output).write_text(
        json.dumps(context_payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _emit(outcome.__dict__)


def _result(args) -> None:
    from .results import validate_and_materialize_result

    validated = validate_and_materialize_result(
        args.machine_result,
        process_exit_code=args.process_exit_code,
        state_root=args.state,
        reports_root=args.reports,
        publishable_destination=args.publishable,
        private_destination=args.private,
    )
    summary = {
        "run_id": validated.result.run_id,
        "status": validated.result.status,
        "exit_code": validated.result.exit_code,
        "state_generation_id": validated.result.state_generation_id,
        "output_generation_id": validated.result.output_generation_id,
    }
    Path(args.summary_output).write_text(
        json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _emit(summary)
    if args.github_output:
        _write_github_output(summary)


def _export(args) -> None:
    from .checkpoint import CheckpointContext, export_checkpoint

    raw = json.loads(Path(args.context).read_text(encoding="utf-8"))
    context = CheckpointContext(
        source_result_status="succeeded",
        source_engine_run_id=args.engine_run_id,
        engine_repository=args.engine_repository,
        engine_sha=args.engine_sha,
        workspace_repository=raw["repository"],
        workspace_repository_id=raw["repository_id"],
        caller_workflow_id=raw["workflow_id"],
        caller_workflow_path=raw["workflow_path"],
        caller_event=raw["event"],
        caller_run_id=raw["current_run_id"],
        caller_run_attempt=raw["run_attempt"],
        caller_ref=raw["ref"],
        config_fingerprint_sha256=args.config_fingerprint,
    )
    export_checkpoint(args.state, args.destination, context)
    _emit({"checkpoint": str(Path(args.destination))})


def _emit(value: dict) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _write_github_output(value: dict) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        raise ValueError("GITHUB_OUTPUT is unavailable")
    with Path(destination).open("a", encoding="utf-8") as stream:
        for key, item in value.items():
            text = str(item)
            if "\n" in text or "\r" in text:
                raise ValueError("GitHub output values must be single-line")
            stream.write(f"{key.replace('_', '-')}={text}\n")


__all__ = ["EXPECTED_ENGINE_REPOSITORY", "main"]
