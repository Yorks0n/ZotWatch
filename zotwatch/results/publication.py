from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from typing import Callable
from xml.etree import ElementTree

from .models import ArtifactReference


_ALLOWED = {
    "recommendations.json": "application/json",
    "feed.xml": "application/rss+xml",
    "report.html": "text/html; charset=utf-8",
}


class PublicationError(RuntimeError):
    """Sanitized output render/publication boundary error."""


@dataclass(frozen=True)
class PublishedGeneration:
    generation_id: str
    artifacts: tuple[ArtifactReference, ...]


class OutputPublisher:
    def __init__(self, reports_root: Path | str):
        self.root = Path(reports_root)
        self.internal = self.root / ".zotwatch-output"

    def publish(
        self,
        run_id: str,
        renderers: dict[str, Callable[[Path], object]],
    ) -> PublishedGeneration:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
            raise PublicationError("Invalid output generation identifier")
        if not renderers or any(name not in _ALLOWED for name in renderers):
            raise PublicationError("Output artifact is not in the publication allowlist")
        staging = self.internal / "staging" / f"{run_id}.tmp"
        generation = self.internal / "generations" / run_id
        if generation.exists():
            raise PublicationError("Output generation already exists")
        try:
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            for name, renderer in renderers.items():
                target = staging / name
                renderer(target)
                self._validate(name, target)
            artifacts = tuple(self._reference(run_id, name, staging / name) for name in renderers)
            generation.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, generation)
            pointer = {
                "schema_version": 1,
                "generation_id": run_id,
                "artifacts": [item.model_dump(mode="json") for item in artifacts],
            }
            self._atomic_bytes(
                self.internal / "latest-success.json",
                (json.dumps(pointer, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            )
            for name in renderers:
                self._atomic_bytes(self.root / name, (generation / name).read_bytes())
            return PublishedGeneration(run_id, artifacts)
        except PublicationError:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise PublicationError("Output generation could not be rendered or published") from exc

    @staticmethod
    def _validate(name: str, path: Path) -> None:
        if not path.is_file():
            raise PublicationError("Output renderer did not create its artifact")
        try:
            if name == "recommendations.json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("schema_name") != "zotwatch-recommendations" or payload.get("schema_version") != 1:
                    raise PublicationError("Recommendation output contract is invalid")
            elif name == "feed.xml":
                ElementTree.parse(path)
            else:
                path.read_text(encoding="utf-8")
        except PublicationError:
            raise
        except Exception as exc:
            raise PublicationError("Rendered output is invalid") from exc

    @staticmethod
    def _reference(run_id: str, name: str, path: Path) -> ArtifactReference:
        content = path.read_bytes()
        return ArtifactReference(
            path=f".zotwatch-output/generations/{run_id}/{name}",
            sha256=sha256(content).hexdigest(),
            size_bytes=len(content),
            media_type=_ALLOWED[name],
            publishable=True,
        )

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)


__all__ = ["OutputPublisher", "PublicationError", "PublishedGeneration"]
