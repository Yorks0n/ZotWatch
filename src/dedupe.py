from __future__ import annotations

import logging
import re
from typing import Iterable, List, Set

from rapidfuzz import fuzz

from .models import CandidateWork
from .storage import ProfileStorage

logger = logging.getLogger(__name__)


class DedupeEngine:
    def __init__(self, storage: ProfileStorage, title_threshold: float = 0.9):
        self.storage = storage
        self.title_threshold = title_threshold
        self.existing_doi: Set[str] = set()
        self.existing_ids: Set[str] = set()
        self.existing_titles: List[str] = []
        self._load_existing()

    def _load_existing(self) -> None:
        for item in self.storage.iter_items():
            if item.doi:
                self.existing_doi.add(_normalize_identifier(item.doi))
            if item.url:
                self.existing_ids.add(_normalize_identifier(item.url))
            self.existing_titles.append(_normalize_title(item.title))

    def filter(self, candidates: Iterable[CandidateWork]) -> List[CandidateWork]:
        source = list(candidates)
        deduped: List[CandidateWork] = []
        candidate_titles: List[str] = []
        seen_keys: Set[str] = set()

        for work in source:
            key = _normalize_identifier(work.identifier)
            doi = _normalize_identifier(work.doi) if work.doi else None
            title = _normalize_title(work.title)

            if doi and doi in self.existing_doi:
                logger.debug("Skipping %s due to DOI duplication", work.identifier)
                continue
            if doi and doi in seen_keys:
                continue
            if key in self.existing_ids or key in seen_keys:
                logger.debug("Skipping %s due to identifier duplication", work.identifier)
                continue
            if self._is_title_duplicate(title) or _is_title_in_list(title, candidate_titles, self.title_threshold):
                logger.debug("Skipping %s due to title similarity", work.identifier)
                continue

            deduped.append(work)
            candidate_titles.append(title)
            seen_keys.add(key)
            if doi:
                seen_keys.add(doi)
        logger.info("Deduped candidates from %d to %d", len(source), len(deduped))
        return deduped

    def _is_title_duplicate(self, title: str) -> bool:
        return _is_title_in_list(title, self.existing_titles, self.title_threshold)


def _normalize_identifier(value: str) -> str:
    return (value or "").lower().strip()


def _normalize_title(title: str) -> str:
    normalized = re.sub(r"\s+", " ", title or "").strip().lower()
    return normalized


def _is_title_in_list(title: str, title_list: Iterable[str], threshold: float) -> bool:
    for existing in title_list:
        if not existing:
            continue
        score = fuzz.token_set_ratio(title, existing) / 100.0
        if score >= threshold:
            return True
    return False


__all__ = ["DedupeEngine"]
