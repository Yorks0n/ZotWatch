from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

import requests

from .models import ZoteroItem
from .settings import Settings
from .storage import ProfileStorage
from .utils import hash_content

logger = logging.getLogger(__name__)

API_BASE = "https://api.zotero.org"


@dataclass
class IngestStats:
    fetched: int = 0
    updated: int = 0
    removed: int = 0
    last_modified_version: Optional[int] = None


class ZoteroClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        api_key = settings.zotero.api.api_key()
        self.session.headers.update(
            {
                "Zotero-API-Version": "3",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "ZotWatcher/0.1",
            }
        )
        self.base_user_url = f"{API_BASE}/users/{settings.zotero.api.user_id}"
        self.base_items_url = f"{self.base_user_url}/items"
        self.polite_delay = settings.zotero.api.polite_delay_ms / 1000

    def iter_items(self, since_version: Optional[int] = None) -> Iterable[requests.Response]:
        params = {
            "limit": self.settings.zotero.api.page_size,
            "sort": "dateAdded",
            "direction": "asc",
        }
        headers = {}
        if since_version is not None:
            headers["If-Modified-Since-Version"] = str(since_version)

        next_url = self.base_items_url
        while next_url:
            logger.debug("Fetching Zotero page: %s", next_url)
            resp = self.session.get(next_url, params=params if next_url == self.base_items_url else None, headers=headers)
            if resp.status_code == 304:
                logger.info("Zotero API indicated no changes since version %s", since_version)
                return
            resp.raise_for_status()
            yield resp
            next_url = _parse_next_link(resp.headers.get("Link"))
            headers = {}
            params = {}
            time.sleep(self.polite_delay)

    def fetch_deleted(self, since_version: Optional[int]) -> List[str]:
        if since_version is None:
            return []
        url = f"{self.base_user_url}/deleted"
        resp = self.session.get(url, params={"since": since_version})
        resp.raise_for_status()
        payload = resp.json() or {}
        deleted_items = payload.get("items", [])
        logger.info("Fetched %d deleted item tombstones", len(deleted_items))
        return deleted_items


def _parse_next_link(link_header: Optional[str]) -> Optional[str]:
    if not link_header:
        return None
    parts = [part.strip() for part in link_header.split(",")]
    for part in parts:
        if "rel=\"next\"" in part:
            url_part = part.split(";")[0].strip()
            if url_part.startswith("<") and url_part.endswith(">"):
                return url_part[1:-1]
    return None


class ZoteroIngestor:
    def __init__(self, storage: ProfileStorage, settings: Settings):
        self.storage = storage
        self.settings = settings
        self.client = ZoteroClient(settings)

    def run(self, *, full: bool = False) -> IngestStats:
        stats = IngestStats()
        self.storage.initialize()
        since_version = None if full else self.storage.last_modified_version()
        logger.info("Starting Zotero ingest (full=%s, since_version=%s)", full, since_version)
        max_version = since_version or 0

        for response in self.client.iter_items(since_version=since_version):
            items = response.json()
            response_version = int(response.headers.get("Last-Modified-Version", 0))
            max_version = max(max_version, response_version)
            for raw_item in items:
                zot_item = ZoteroItem.from_zotero_api(raw_item)
                content_hash = hash_content(
                    zot_item.title,
                    zot_item.abstract or "",
                    ",".join(zot_item.creators),
                    ",".join(zot_item.tags),
                )
                self.storage.upsert_item(zot_item, content_hash=content_hash)
                stats.fetched += 1
                stats.updated += 1

        deleted_keys = self.client.fetch_deleted(since_version=max_version if not full else None)
        self.storage.remove_items(deleted_keys)
        stats.removed = len(deleted_keys)

        if stats.fetched or full:
            stats.last_modified_version = max_version
            if max_version:
                self.storage.set_last_modified_version(max_version)
                logger.info("Updated last modified version to %s", max_version)

        return stats


__all__ = ["ZoteroIngestor", "IngestStats"]
