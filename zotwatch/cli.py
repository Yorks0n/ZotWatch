"""Public console entry point for legacy and Basic v2 workspaces."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _validation_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zotwatch config validate")
    parser.add_argument("--workspace", help="Workspace containing zotwatch.yaml or legacy config")
    return parser


def _preflight_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zotwatch runtime preflight")
    parser.add_argument("--workspace", help="Workspace containing ZotWatch configuration")
    parser.add_argument("--json", action="store_true", help="Emit the redacted v1 report")
    parser.add_argument("--verify-zotero", action="store_true", help="Verify Zotero explicitly")
    return parser


def _v2_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zotwatch")
    parser.add_argument("command", choices=["profile", "watch"])
    parser.add_argument("--workspace")
    parser.add_argument("--base-dir")
    parser.add_argument("--state-dir")
    parser.add_argument("--reports-dir")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--rss", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--top", type=int)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--journal-metrics")
    return parser


def _workspace_from_legacy_args(argv: list[str]) -> Path:
    selected: str | None = None
    for index, value in enumerate(argv):
        if value in {"--workspace", "--base-dir"} and index + 1 < len(argv):
            selected = argv[index + 1]
        elif value.startswith("--workspace=") or value.startswith("--base-dir="):
            selected = value.split("=", 1)[1]
    return Path(selected).resolve() if selected else Path.cwd().resolve()


def _validate(argv: list[str]) -> int:
    from zotwatch.config import ConfigSource, load_workspace_config

    args = _validation_parser().parse_args(argv)
    workspace = Path(args.workspace).resolve() if args.workspace else Path.cwd().resolve()
    loaded = load_workspace_config(workspace)
    if loaded.source is ConfigSource.V2:
        available = sum(route.runtime_available for route in loaded.routes)
        print(
            "Configuration is valid (schema and semantics only). "
            f"AI runtime readiness: {available}/{len(loaded.routes)} enabled features executable."
        )
    else:
        projection = (
            "ordinary v2 projection available"
            if loaded.config is not None
            else "legacy compatibility only"
        )
        print(f"Legacy configuration is valid ({projection}).")
    return 0


def _runtime_preflight(argv: list[str]) -> int:
    from dotenv import load_dotenv
    from zotwatch.runtime import load_effective_runtime, preflight

    args = _preflight_parser().parse_args(argv)
    workspace = Path(args.workspace).resolve() if args.workspace else Path.cwd().resolve()
    load_dotenv(workspace / ".env")
    report = preflight(
        load_effective_runtime(workspace), verify_zotero=args.verify_zotero
    )
    if args.json:
        print(report.model_dump_json())
    elif report.ready:
        print("Runtime preflight is ready.")
    else:
        print(f"Runtime preflight failed: {report.error_code}.", file=sys.stderr)
    return 0 if report.ready else 3


def _has_option(arguments: list[str], option: str) -> bool:
    return option in arguments or any(value.startswith(f"{option}=") for value in arguments)


def _run_v2(arguments: list[str]) -> int:
    from dotenv import load_dotenv
    from src import cli as engine_cli
    from src.logging_utils import setup_logging
    from src.storage import ProfileStorage
    from zotwatch.paths import RuntimePaths
    from zotwatch.runtime import load_effective_runtime, preflight

    args = _v2_parser().parse_args(arguments)
    paths = RuntimePaths.resolve(
        workspace=args.workspace,
        base_dir=args.base_dir,
        state_dir=args.state_dir,
        reports_dir=args.reports_dir,
    )
    load_dotenv(paths.workspace / ".env")
    effective = load_effective_runtime(paths.workspace)
    forbidden = ("--rss", "--report", "--top", "--push", "--journal-metrics")
    if any(_has_option(arguments, option) for option in forbidden):
        print(
            "CONFIG_OPTION_UNSUPPORTED: Basic v2 runtime options come from zotwatch.yaml",
            file=sys.stderr,
        )
        return 2
    report = preflight(effective)
    if not report.ready:
        print(f"{report.error_code}: runtime preflight failed", file=sys.stderr)
        return 3

    setup_logging(verbose=args.verbose)
    storage = ProfileStorage(paths.state / "profile.sqlite")
    try:
        if args.command == "profile":
            engine_cli.run_profile(
                paths.workspace,
                effective.settings,
                storage,
                full=args.full or args.weekly,
                state_dir=paths.state,
            )
        else:
            engine_cli.run_watch(
                paths.workspace,
                effective.settings,
                storage,
                rss="rss" in effective.output_formats,
                report="html" in effective.output_formats,
                top=effective.top_n,
                push=False,
                state_dir=paths.state,
                reports_dir=paths.reports,
                journal_metrics=effective.journal_metrics,
            )
    finally:
        storage.close()
    return 0


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:2] == ["config", "validate"]:
        try:
            return _validate(arguments[2:])
        except Exception as exc:
            from zotwatch.config import ConfigError

            if not isinstance(exc, ConfigError):
                raise
            print(str(exc), file=sys.stderr)
            return 2

    if arguments[:2] == ["runtime", "preflight"]:
        try:
            return _runtime_preflight(arguments[2:])
        except Exception as exc:
            from zotwatch.config import ConfigError

            if not isinstance(exc, ConfigError):
                raise
            print(str(exc), file=sys.stderr)
            return 2

    if arguments and arguments[0] in {"profile", "watch"}:
        from zotwatch.config import ConfigError, ConfigSource, detect_config_source

        try:
            source = detect_config_source(_workspace_from_legacy_args(arguments))
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if source is ConfigSource.V2:
            try:
                return _run_v2(arguments)
            except ValueError as exc:
                print(f"CONFIG_INVALID: {exc}", file=sys.stderr)
                return 2

    from src.cli import main as legacy_main

    return legacy_main(arguments)
