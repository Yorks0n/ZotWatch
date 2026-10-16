from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Set

import requests

from .computational_state import StateLease
from .http_utils import request_with_retry
from .models import ZoteroItem
from .settings import Settings
from .storage import ProfileStorage
from .utils import hash_content

logger = logging.getLogger(__name__)

API_BASE = "https://api.zotero.org"
REVISION_RESTART_ATTEMPTS = 3
REVISION_RESTART_BACKOFF_SECONDS = 0.25


class ZoteroSyncError(RuntimeError):
    """A sync attempt could not establish and commit a complete library revision."""


class ZoteroProtocolError(ZoteroSyncError):
    """A Zotero response could not be safely interpreted."""


class ZoteroRevisionChanged(ZoteroSyncError):
    """The remote library revision changed while a snapshot was being acquired."""


@dataclass
class IngestStats:
    # Legacy counters. ``updated`` intentionally remains a processed-record count.
    fetched: int = 0
    updated: int = 0
    removed: int = 0
    last_modified_version: Optional[int] = None

    # E3 revision state and actual SQLite mutation counters.
    start_revision: Optional[int] = None
    target_revision: Optional[int] = None
    observed_revision: Optional[int] = None
    committed_revision: Optional[int] = None
    applied_inserted: int = 0
    applied_changed: int = 0
    applied_removed: int = 0


@dataclass(frozen=True)
class StagedRemoteItem:
    item: ZoteroItem
    content_hash: str
    trashed: bool
    raw_signature: str


@dataclass
class AcquisitionResult:
    start_revision: Optional[int]
    target_revision: int
    observed_revision: int
    full: bool
    no_op: bool = False
    items: Dict[str, StagedRemoteItem] = field(default_factory=dict)
    deleted_keys: Set[str] = field(default_factory=set)
    fetched: int = 0
    updated: int = 0

    @property
    def active_items(self) -> Dict[str, StagedRemoteItem]:
        return {key: staged for key, staged in self.items.items() if not staged.trashed}

    @property
    def trashed_keys(self) -> Set[str]:
        return {key for key, staged in self.items.items() if staged.trashed}


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
            # Transport includes trash so move/restore transitions are observable.
            "includeTrashed": 1,
        }
        headers = {}
        if since_version is not None:
            params["since"] = since_version
            headers["If-Modified-Since-Version"] = str(since_version)

        next_url = self.base_items_url
        visited_urls: Set[str] = set()
        while next_url:
            if next_url in visited_urls:
                raise ZoteroProtocolError(f"Zotero pagination cycle at {next_url}")
            visited_urls.add(next_url)
            logger.debug("Fetching Zotero page: %s", next_url)
            resp = request_with_retry(
                self.session,
                "GET",
                next_url,
                params=params if next_url == self.base_items_url else None,
                headers=headers,
                timeout=30,
                logger=logger,
                context=f"Zotero items request {next_url}",
            )
            if resp.status_code == 304:
                logger.info("Zotero API indicated no changes since version %s", since_version)
                return
            yield resp
            next_url = _parse_next_link(resp.headers.get("Link"))
            headers = {}
            params = {}
            if next_url:
                time.sleep(self.polite_delay)

    def fetch_deleted(self, since_version: int) -> requests.Response:
        url = f"{self.base_user_url}/deleted"
        return request_with_retry(
            self.session,
            "GET",
            url,
            params={"since": since_version},
            timeout=30,
            logger=logger,
            context=f"Zotero deleted-items request since version {since_version}",
        )


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


def _response_revision(response: requests.Response, *, context: str) -> int:
    value = response.headers.get("Last-Modified-Version")
    if value is None:
        raise ZoteroProtocolError(f"{context} omitted Last-Modified-Version")
    try:
        revision = int(value)
    except (TypeError, ValueError) as exc:
        raise ZoteroProtocolError(f"{context} returned invalid Last-Modified-Version") from exc
    if revision < 0:
        raise ZoteroProtocolError(f"{context} returned a negative Last-Modified-Version")
    return revision


def _response_json(response: requests.Response, *, context: str):
    try:
        return response.json()
    except (TypeError, ValueError) as exc:
        raise ZoteroProtocolError(f"{context} returned invalid JSON") from exc


def _stage_remote_item(raw_item: object) -> StagedRemoteItem:
    if not isinstance(raw_item, dict):
        raise ZoteroProtocolError("Zotero item entry must be an object")
    data = raw_item.get("data")
    if not isinstance(data, dict):
        raise ZoteroProtocolError("Zotero item data must be an object")

    top_key = raw_item.get("key")
    data_key = data.get("key")
    if top_key is not None and data_key is not None and top_key != data_key:
        raise ZoteroProtocolError("Zotero item top-level and data keys differ")
    key = data_key if data_key is not None else top_key
    if not isinstance(key, str) or not key.strip():
        raise ZoteroProtocolError("Zotero item key must be a non-empty string")

    top_version = raw_item.get("version")
    data_version = data.get("version")
    if top_version is not None and data_version is not None and top_version != data_version:
        raise ZoteroProtocolError(f"Zotero item {key} has conflicting versions")
    version = data_version if data_version is not None else top_version
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ZoteroProtocolError(f"Zotero item {key} has an invalid version")

    if "deleted" in data:
        deleted = data["deleted"]
        if deleted is True:
            trashed = True
        elif isinstance(deleted, int) and not isinstance(deleted, bool) and deleted == 1:
            trashed = True
        else:
            raise ZoteroProtocolError(f"Zotero item {key} has an invalid trash marker")
    else:
        trashed = False

    try:
        signature = json.dumps(raw_item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        zot_item = ZoteroItem.from_zotero_api(raw_item)
    except Exception as exc:
        raise ZoteroProtocolError(f"Zotero item {key} could not be projected") from exc
    content_hash = hash_content(
        zot_item.title,
        zot_item.abstract or "",
        ",".join(zot_item.creators),
        ",".join(zot_item.tags),
    )
    return StagedRemoteItem(
        item=zot_item,
        content_hash=content_hash,
        trashed=trashed,
        raw_signature=signature,
    )


class ZoteroIngestor:
    def __init__(self, storage: ProfileStorage, settings: Settings):
        self.storage = storage
        self.settings = settings
        self.client = ZoteroClient(settings)

    def run(
        self,
        *,
        full: bool = False,
        library_identity_sha256: str | None = None,
        lease: StateLease | None = None,
    ) -> IngestStats:
        if lease is not None:
            lease.validate_for(self.storage.path.parent)
        self.storage.initialize()
        stored_identity = self.storage.library_identity_sha256()
        if library_identity_sha256 is not None and stored_identity != library_identity_sha256:
            full = True
        start_revision = self._read_start_revision(full=full)
        snapshot_mode = full or start_revision is None
        logger.info(
            "Starting Zotero ingest (full=%s, start_revision=%s)",
            snapshot_mode,
            start_revision,
        )

        try:
            result = self._acquire_with_revision_restarts(
                start_revision=start_revision,
                full=snapshot_mode,
            )
        except ZoteroSyncError:
            raise
        except requests.RequestException as exc:
            raise ZoteroSyncError("Zotero sync failed before a complete revision was acquired") from exc

        stats = IngestStats(
            fetched=result.fetched,
            updated=result.updated,
            start_revision=start_revision,
            target_revision=result.target_revision,
            observed_revision=result.observed_revision,
        )
        if result.no_op:
            stats.last_modified_version = start_revision
            stats.committed_revision = start_revision
            return stats

        active_items = result.active_items
        removal_keys = result.trashed_keys | result.deleted_keys
        apply_result = self.storage.apply_zotero_sync(
            ((staged.item, staged.content_hash) for staged in active_items.values()),
            removal_keys,
            full=result.full,
            revision=result.target_revision,
            library_identity_sha256=library_identity_sha256,
        )

        stats.removed = apply_result.removed if result.full else len(removal_keys)
        stats.last_modified_version = result.target_revision
        stats.committed_revision = result.target_revision
        stats.applied_inserted = apply_result.inserted
        stats.applied_changed = apply_result.changed
        stats.applied_removed = apply_result.removed
        return stats

    def _read_start_revision(self, *, full: bool) -> Optional[int]:
        try:
            return self.storage.last_modified_version()
        except (TypeError, ValueError) as exc:
            if full:
                logger.warning("Ignoring invalid stored Zotero revision for explicit full sync")
                return None
            raise ZoteroSyncError(
                "Stored Zotero last_modified_version is invalid; run an explicit full sync"
            ) from exc

    def _acquire_with_revision_restarts(
        self,
        *,
        start_revision: Optional[int],
        full: bool,
    ) -> AcquisitionResult:
        for attempt in range(1, REVISION_RESTART_ATTEMPTS + 1):
            try:
                return self._acquire_once(start_revision=start_revision, full=full)
            except ZoteroRevisionChanged:
                if attempt == REVISION_RESTART_ATTEMPTS:
                    raise
                delay = REVISION_RESTART_BACKOFF_SECONDS * attempt
                logger.warning(
                    "Zotero library changed during sync attempt %d/%d; restarting in %.2fs",
                    attempt,
                    REVISION_RESTART_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
        raise AssertionError("unreachable")

    def _acquire_once(
        self,
        *,
        start_revision: Optional[int],
        full: bool,
    ) -> AcquisitionResult:
        request_revision = None if full else start_revision
        target_revision: Optional[int] = None
        observed_revision: Optional[int] = None
        staged: Dict[str, StagedRemoteItem] = {}
        fetched = 0
        updated = 0

        for page_number, response in enumerate(
            self.client.iter_items(since_version=request_revision), start=1
        ):
            revision = _response_revision(response, context=f"Zotero items page {page_number}")
            if target_revision is None:
                target_revision = revision
                if start_revision is not None and revision < start_revision:
                    raise ZoteroProtocolError(
                        "Zotero target revision is older than the committed revision"
                    )
            elif revision != target_revision:
                raise ZoteroRevisionChanged(
                    f"Zotero items revision changed from {target_revision} to {revision}"
                )
            observed_revision = revision

            payload = _response_json(response, context=f"Zotero items page {page_number}")
            if not isinstance(payload, list):
                raise ZoteroProtocolError("Zotero items response must be a list")
            for raw_item in payload:
                remote = _stage_remote_item(raw_item)
                fetched += 1
                if not remote.trashed:
                    updated += 1
                existing = staged.get(remote.item.key)
                if existing is None or remote.item.version > existing.item.version:
                    staged[remote.item.key] = remote
                elif remote.item.version == existing.item.version:
                    if remote.raw_signature != existing.raw_signature:
                        raise ZoteroProtocolError(
                            f"Zotero item {remote.item.key} has conflicting payloads at one version"
                        )
                # A lower-version replay is ignored but remains part of legacy counts.

        if target_revision is None:
            if full:
                raise ZoteroProtocolError("Full Zotero sync returned no versioned response")
            assert start_revision is not None
            return AcquisitionResult(
                start_revision=start_revision,
                target_revision=start_revision,
                observed_revision=start_revision,
                full=False,
                no_op=True,
            )

        deleted_keys: Set[str] = set()
        if not full:
            assert start_revision is not None
            deleted_response = self.client.fetch_deleted(start_revision)
            deleted_revision = _response_revision(
                deleted_response, context="Zotero deleted-items response"
            )
            if deleted_revision != target_revision:
                raise ZoteroRevisionChanged(
                    f"Zotero deleted-items revision changed from {target_revision} "
                    f"to {deleted_revision}"
                )
            observed_revision = deleted_revision
            deleted_payload = _response_json(
                deleted_response, context="Zotero deleted-items response"
            )
            if not isinstance(deleted_payload, dict):
                raise ZoteroProtocolError("Zotero deleted-items response must be an object")
            raw_deleted = deleted_payload.get("items", [])
            if not isinstance(raw_deleted, list):
                raise ZoteroProtocolError("Zotero deleted item keys must be a list")
            for key in raw_deleted:
                if not isinstance(key, str) or not key.strip():
                    raise ZoteroProtocolError("Zotero deleted item key must be a non-empty string")
                deleted_keys.add(key)

        assert observed_revision is not None
        return AcquisitionResult(
            start_revision=start_revision,
            target_revision=target_revision,
            observed_revision=observed_revision,
            full=full,
            items=staged,
            deleted_keys=deleted_keys,
            fetched=fetched,
            updated=updated,
        )


__all__ = [
    "ZoteroIngestor",
    "ZoteroClient",
    "IngestStats",
    "ZoteroSyncError",
    "ZoteroProtocolError",
    "ZoteroRevisionChanged",
]
