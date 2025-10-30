from __future__ import annotations

import logging
from typing import Iterable, Optional

import requests

from .models import RankedWork
from .settings import Settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.zotero.org"
COLLECTION_NAME = "AI Suggested"


class ZoteroPusher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        api_key = settings.zotero.api.api_key()
        self.session.headers.update(
            {
                "Zotero-API-Version": "3",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "ZotWatcher/0.1",
                "Content-Type": "application/json",
            }
        )
        self.base_url = f"{API_BASE}/users/{settings.zotero.api.user_id}"
        self._collection_key: Optional[str] = None

    def push(self, works: Iterable[RankedWork], note_template: str | None = None) -> None:
        works_list = list(works)
        if not works_list:
            logger.info("No works provided for Zotero push")
            return
        collection_key = self._ensure_collection()
        payload = []
        for work in works_list:
            note = (note_template or "Recommended due to score {score:.3f}").format(**work.dict())
            payload.append(
                {
                    "itemType": "note",
                    "note": note,
                    "tags": [
                        {"tag": "ZotWatcher"},
                        {"tag": work.label},
                    ],
                    "collections": [collection_key],
                }
            )
        url = f"{self.base_url}/items"
        logger.info("Pushing %d recommendation notes to Zotero", len(payload))
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        logger.info("Successfully pushed notes to Zotero collection %s", collection_key)

    def _ensure_collection(self) -> str:
        if self._collection_key:
            return self._collection_key
        collections_url = f"{self.base_url}/collections"
        resp = self.session.get(collections_url, params={"limit": 100})
        resp.raise_for_status()
        for collection in resp.json():
            data = collection.get("data", {})
            if data.get("name") == COLLECTION_NAME:
                self._collection_key = data.get("key")
                return self._collection_key

        # create collection
        logger.info("Creating Zotero collection '%s'", COLLECTION_NAME)
        resp = self.session.post(collections_url, json=[{"name": COLLECTION_NAME}])
        resp.raise_for_status()
        created = resp.json()[0]
        self._collection_key = created.get("successful", {}).get("0", {}).get("data", {}).get("key")
        if not self._collection_key:
            raise RuntimeError("Failed to create or retrieve Zotero collection")
        return self._collection_key


__all__ = ["ZoteroPusher"]
