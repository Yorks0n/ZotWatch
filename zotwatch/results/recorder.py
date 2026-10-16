from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from .errors import run_error
from .models import ArtifactReference, RunManifest, RunResult, StageRecord


PROFILE_STAGES = (
    "config_validation", "credential_preflight", "zotero_sync", "computational_state",
)
WATCH_STAGES = PROFILE_STAGES + (
    "candidate_fetch", "dedupe", "ranking", "output_render", "zotero_writeback",
    "output_publish",
)


def utc_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RunRecorder:
    def __init__(
        self,
        state_root: Path | str,
        command: str,
        *,
        config_schema_version: int | None,
        config_fingerprint_sha256: str | None,
        run_id: str | None = None,
    ):
        self.state_root = Path(state_root)
        self.command = command
        self.run_id = run_id or uuid4().hex
        self.generated_at = utc_text()
        self.config_schema_version = config_schema_version
        self.config_fingerprint = config_fingerprint_sha256
        names = PROFILE_STAGES if command == "profile" else WATCH_STAGES
        self.stages = {name: StageRecord(stage=name, status="pending") for name in names}

    def start(self, stage: str) -> None:
        self.stages[stage] = self.stages[stage].model_copy(
            update={"status": "running", "started_at": utc_text()}
        )

    def finish(self, stage: str, *, count: int | None = None) -> None:
        current = self.stages[stage]
        self.stages[stage] = current.model_copy(update={
            "status": "succeeded", "started_at": current.started_at or utc_text(),
            "finished_at": utc_text(), "count": count,
        })

    def degrade(self, stage: str, code: str, *, count: int | None = None) -> None:
        current = self.stages[stage]
        self.stages[stage] = current.model_copy(update={
            "status": "degraded", "started_at": current.started_at or utc_text(),
            "finished_at": utc_text(), "count": count, "error": run_error(code),
        })

    def fail(self, stage: str, code: str) -> None:
        current = self.stages[stage]
        self.stages[stage] = current.model_copy(update={
            "status": "failed", "started_at": current.started_at or utc_text(),
            "finished_at": utc_text(), "error": run_error(code),
        })

    def finalize(
        self,
        *,
        status: str,
        exit_code: int,
        error_code: str | None = None,
        state_generation_id: str | None = None,
        output_generation_id: str | None = None,
        artifacts: tuple[ArtifactReference, ...] = (),
    ) -> RunResult:
        stages = []
        for stage in self.stages.values():
            if stage.status in {"pending", "running"}:
                stage = stage.model_copy(update={"status": "skipped", "finished_at": utc_text()})
            stages.append(stage)
        error = run_error(error_code) if error_code else None
        manifest = RunManifest(
            run_id=self.run_id,
            command=self.command,
            status=status,
            exit_code=exit_code,
            generated_at=self.generated_at,
            config_schema_version=self.config_schema_version,
            config_fingerprint_sha256=self.config_fingerprint,
            state_generation_id=state_generation_id,
            output_generation_id=output_generation_id,
            stages=stages,
            artifacts=list(artifacts),
            error=error,
        )
        relative = f"runs/{self.run_id}.json"
        path = self.state_root / relative
        self._atomic_json(path, manifest.model_dump(mode="json"))
        self._atomic_json(
            self.state_root / "runs/latest-attempt.json",
            {"schema_version": 1, "run_id": self.run_id, "manifest_path": relative},
        )
        return RunResult(
            run_id=self.run_id,
            status=status,
            exit_code=exit_code,
            error=error,
            manifest_path=relative,
            state_generation_id=state_generation_id,
            output_generation_id=output_generation_id,
            artifacts=list(artifacts),
        )

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)


__all__ = ["RunRecorder", "utc_text"]
