from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

from pydantic import BaseModel, Field


class ZoteroItem(BaseModel):
    key: str
    version: int
    title: str
    abstract: Optional[str] = None
    creators: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    collections: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    raw: Dict[str, object] = Field(default_factory=dict)

    def content_for_embedding(self) -> str:
        parts = [self.title]
        if self.abstract:
            parts.append(self.abstract)
        if self.creators:
            parts.append("; ".join(self.creators))
        if self.tags:
            parts.append("; ".join(self.tags))
        return "\n".join(filter(None, parts))

    @classmethod
    def from_zotero_api(cls, item: Dict[str, object]) -> "ZoteroItem":
        data = item.get("data", {})
        creators = [
            " ".join(filter(None, [c.get("firstName"), c.get("lastName")])).strip()
            for c in data.get("creators", [])
        ]
        return cls(
            key=data.get("key") or item.get("key"),
            version=data.get("version") or item.get("version", 0),
            title=data.get("title") or "",
            abstract=data.get("abstractNote"),
            creators=[c for c in creators if c],
            tags=[t.get("tag") for t in data.get("tags", []) if isinstance(t, dict)],
            collections=data.get("collections", []),
            year=_safe_int(data.get("date")),
            doi=data.get("DOI"),
            url=data.get("url"),
            raw=item,
        )


def _safe_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    for part in value.split("-"):
        if part.isdigit():
            return int(part)
    return None


class CandidateWork(BaseModel):
    source: str
    identifier: str
    title: str
    abstract: Optional[str] = None
    authors: List[str] = Field(default_factory=list)
    doi: Optional[str] = None
    url: Optional[str] = None
    published: Optional[datetime] = None
    venue: Optional[str] = None
    metrics: Dict[str, float] = Field(default_factory=dict)
    extra: Dict[str, object] = Field(default_factory=dict)

    def content_for_embedding(self) -> str:
        parts = [self.title]
        if self.abstract:
            parts.append(self.abstract)
        if self.authors:
            parts.append("; ".join(self.authors))
        return "\n".join(filter(None, parts))


class RankedWork(CandidateWork):
    score: float
    similarity: float
    recency_score: float
    metric_score: float
    author_bonus: float
    venue_bonus: float
    journal_quality: float = 1.0
    journal_sjr: Optional[float] = None
    label: str


@dataclass
class ProfileArtifacts:
    sqlite_path: str
    faiss_path: str
    profile_json_path: str


def iter_batches(items: Sequence, batch_size: int) -> Iterable[Sequence]:
    for i in range(0, len(items), batch_size):
        yield items[i : i + batch_size]


__all__ = [
    "ZoteroItem",
    "CandidateWork",
    "RankedWork",
    "ProfileArtifacts",
    "iter_batches",
]
