"""Public console entry point with an offline v2 configuration command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _validation_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zotwatch config validate")
    parser.add_argument("--workspace", help="Workspace containing zotwatch.yaml or legacy config")
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

    if arguments and arguments[0] in {"profile", "watch"}:
        from zotwatch.config import ConfigError, ConfigSource, detect_config_source

        try:
            source = detect_config_source(_workspace_from_legacy_args(arguments))
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if source is ConfigSource.V2:
            error = ConfigError(
                "CONFIG_V2_EXECUTION_DEFERRED",
                "E2 validates zotwatch.yaml but does not connect it to recommendation execution",
            )
            print(str(error), file=sys.stderr)
            return 2

    from src.cli import main as legacy_main

    return legacy_main(arguments)
