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
