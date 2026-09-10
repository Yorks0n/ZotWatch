"""Resolve personal paths independently of the installed engine location."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    workspace: Path
    state: Path
    reports: Path

    @classmethod
    def resolve(cls, *, workspace=None, base_dir=None, state_dir=None, reports_dir=None):
        workspace_path = Path(workspace).resolve() if workspace is not None else None
        legacy_path = Path(base_dir).resolve() if base_dir is not None else None
        if workspace_path is not None and legacy_path is not None and workspace_path != legacy_path:
            raise ValueError("--workspace and --base-dir must identify the same workspace")
        root = workspace_path or legacy_path or Path.cwd()

        def relative_to_workspace(value, default):
            return (root / (Path(value) if value is not None else default)).resolve()

        return cls(root, relative_to_workspace(state_dir, "data"),
                   relative_to_workspace(reports_dir, "reports"))
