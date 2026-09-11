"""Explicit access to engine-owned resources; legacy selection stays unchanged."""
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path


@contextmanager
def journal_metrics_path(selection: str, workspace: Path):
    if selection == "legacy":
        yield None  # Let the legacy ranker retain missing-file behavior.
    elif selection == "bundled":
        with as_file(files(__package__).joinpath("journal_metrics.csv")) as path:
            yield path
    else:
        yield (workspace / selection).resolve()


@contextmanager
def contract_schema_path(name: str):
    if name not in {
        "recommendations-v1.schema.json",
        "run-manifest-v1.schema.json",
        "run-result-v1.schema.json",
    }:
        raise ValueError("Unknown ZotWatch contract schema")
    with as_file(files(__package__).joinpath(name)) as path:
        yield path
