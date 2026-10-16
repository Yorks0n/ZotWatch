from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
import json


@dataclass(frozen=True)
class PublicCandidateConnection:
    provider: str
    base_url: str = field(repr=False)
    publishable_key: str = field(repr=False)
    page_size: int
    timeout_seconds: int


def load_public_candidate_connection() -> PublicCandidateConnection:
    resource = files("zotwatch.resources").joinpath("public-candidates-v1.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    expected = {"provider", "base_url", "publishable_key", "page_size", "timeout_seconds"}
    if set(payload) != expected or payload.get("provider") != "public-api-v1":
        raise RuntimeError("Invalid engine-owned public candidate connection resource")
    return PublicCandidateConnection(**payload)


__all__ = ["PublicCandidateConnection", "load_public_candidate_connection"]
