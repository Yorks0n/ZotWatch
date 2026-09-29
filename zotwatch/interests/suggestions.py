"""Bounded, rebuildable review suggestions; never confirmed ranking input."""
from __future__ import annotations

from collections import defaultdict
from itertools import islice
import json
from pathlib import Path
import re
import unicodedata
from typing import Literal

from pydantic import Field, model_validator
from .contract import ClosedModel, SHA, InterestError

MAX_ITEMS = 5000
MAX_BYTES = 32768
ARTIFACT_NAME = "zotwatch-interest-suggestions-v1"
SUMMARY_FILE = "interest-suggestions-v1.json"
GENERATOR = "bounded-keywords-v1"
STOP = frozenset("a an and are as at be been by can data for from has have in into is it its method methods new of on or our paper research result results study studies that the their these this to using via was we were which with analysis approach based model models system systems information general review article journal et al not no more other than then they used use also about such through between within without learning".split())
# Conservative Latin word tokens only: unsegmented CJK prose is not a keyword
# and must never be copied as an abstract/title excerpt. Unicode tags remain usable.
WORD = re.compile(r"(?<!\w)[a-z]{3,32}(?!\w)")


class Suggestion(ClosedModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    source_types: list[Literal["tag", "text_keyword"]] = Field(min_length=1, max_length=2)
    supporting_items: int = Field(ge=2, le=MAX_ITEMS)
    keywords: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def bounded_terms(self):
        if len(set(self.source_types)) != len(self.source_types) or len(set(self.keywords)) != len(self.keywords):
            raise ValueError("Duplicate suggestion evidence")
        if any(not 2 <= len(term) <= 60 or not all(c.isalpha() or c in " -" for c in term) for term in self.keywords):
            raise ValueError("Invalid suggestion keyword")
        return self


class Projection(ClosedModel):
    generator_version: Literal["bounded-keywords-v1"]
    sampled_items: int = Field(ge=0, le=MAX_ITEMS)
    truncated: bool
    suggestions: list[Suggestion] = Field(max_length=12)

    @model_validator(mode="after")
    def counts(self):
        if any(s.supporting_items > self.sampled_items for s in self.suggestions):
            raise ValueError("Invalid support count")
        if len({s.name for s in self.suggestions}) != len(self.suggestions):
            raise ValueError("Duplicate suggestion")
        return self


class Summary(ClosedModel):
    schema_name: Literal["zotwatch-interest-suggestions"]
    schema_version: Literal[1]
    engine_sha: SHA
    workspace_repository_id: int = Field(gt=0, le=9007199254740991)
    caller_run_id: int = Field(gt=0, le=9007199254740991)
    caller_run_attempt: int = Field(gt=0, le=9007199254740991)
    engine_run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    projection: Projection


def _tag(value: str) -> str | None:
    value = " ".join(unicodedata.normalize("NFKC", value[:128]).casefold().split())
    if not 2 <= len(value) <= 60 or len(value.split()) > 4:
        return None
    if not all(c.isalpha() or c in " -" for c in value) or all(w in STOP for w in value.split()):
        return None
    return value


def project(items) -> Projection:
    # Item indexes remain local and are discarded. Counts mean evidence, not confidence.
    supports, sources = defaultdict(set), defaultdict(set)
    sampled, truncated = 0, False
    seen = set()
    for index, item in enumerate(islice(items, MAX_ITEMS + 1)):
        if index == MAX_ITEMS:
            truncated = True
            break
        if item.key in seen:
            continue
        seen.add(item.key)
        sampled += 1
        for raw in item.tags[:32]:
            term = _tag(raw)
            if term:
                supports[term].add(index); sources[term].add("tag")
        text = unicodedata.normalize("NFKC", (item.title + " " + (item.abstract or ""))[:4096]).casefold()
        for term in islice(WORD.findall(text), 128):
            if term not in STOP:
                supports[term].add(index); sources[term].add("text_keyword")
    terms = sorted((t for t in supports if len(supports[t]) >= 2),
                   key=lambda t: (-len(supports[t]), "tag" not in sources[t], t))
    suggestions, consumed = [], set()
    for term in terms:
        if term in consumed:
            continue
        # Merge terms with identical evidence; do not claim semantic clustering.
        group = [t for t in terms if t not in consumed and supports[t] == supports[term]][:5]
        consumed.update(t for t in terms if supports[t] == supports[term])
        kinds = sorted(set().union(*(sources[t] for t in group)))
        suggestions.append(Suggestion(name=term, description="Explore research related to " + ", ".join(group) + ".",
            source_types=kinds, supporting_items=len(supports[term]), keywords=group))
        if len(suggestions) == 12:
            break
    return Projection(generator_version=GENERATOR, sampled_items=sampled, truncated=truncated, suggestions=suggestions)


def write_projection(state: Path, run_id: str, projection: Projection) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise InterestError("SUGGESTION_ID_INVALID")
    target = state / "runs" / f"suggestions-{run_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = projection.model_dump_json().encode() + b"\n"
    if len(data) > MAX_BYTES:
        raise InterestError("SUGGESTION_TOO_LARGE")
    with target.open("xb") as stream:
        stream.write(data)


def export_summary(state: Path, machine: Path, destination: Path, **identity) -> Summary:
    from .results import parse_result
    result = parse_result(machine.read_bytes())
    if result.status != "succeeded":
        raise InterestError("SUGGESTION_RUN_UNAVAILABLE")
    recorded = state / "runs" / f"topic-{result.run_id}.json"
    source = state / "runs" / f"suggestions-{result.run_id}.json"
    if recorded.is_symlink() or recorded.read_bytes() != machine.read_bytes() or source.is_symlink() or source.stat().st_size > MAX_BYTES:
        raise InterestError("SUGGESTION_RUN_INVALID")
    summary = Summary(schema_name="zotwatch-interest-suggestions", schema_version=1, **identity, engine_run_id=result.run_id, projection=Projection.model_validate_json(source.read_bytes()))
    data = summary.model_dump_json().encode() + b"\n"
    if len(data) > MAX_BYTES:
        raise InterestError("SUGGESTION_TOO_LARGE")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / SUMMARY_FILE).write_bytes(data)
    return summary
