from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .computational_state import library_snapshot_fingerprints
from .models import ZoteroItem


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    key TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT,
    creators TEXT,
    tags TEXT,
    collections TEXT,
    year INTEGER,
    doi TEXT,
    url TEXT,
    raw_json TEXT NOT NULL,
    content_hash TEXT,
    embedding BLOB,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_version ON items(version);
"""

SQLITE_DELETE_BATCH_SIZE = 500


@dataclass(frozen=True)
class SyncApplyResult:
    inserted: int = 0
    changed: int = 0
    removed: int = 0


@dataclass(frozen=True)
class ProfileSnapshot:
    library_identity_sha256: str
    revision: int
    items: tuple[ZoteroItem, ...]
    snapshot_sha256: str
    embedding_input_set_sha256: str


class ProfileStorage:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def initialize(self) -> None:
        conn = self.connect()
        conn.executescript(SCHEMA)
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # metadata helpers
    def get_metadata(self, key: str) -> Optional[str]:
        cur = self.connect().execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        self.connect().execute(
            "REPLACE INTO metadata(key, value) VALUES(?, ?)",
            (key, value),
        )
        self.connect().commit()

    def last_modified_version(self) -> Optional[int]:
        value = self.get_metadata("last_modified_version")
        return int(value) if value else None

    def set_last_modified_version(self, version: int) -> None:
        self.set_metadata("last_modified_version", str(version))

    def library_identity_sha256(self) -> Optional[str]:
        return self.get_metadata("library_identity_sha256")

    def set_library_identity_sha256(self, identity: str) -> None:
        self.set_metadata("library_identity_sha256", identity)

    # item helpers
    def upsert_item(self, item: ZoteroItem, content_hash: Optional[str] = None) -> None:
        _execute_upsert(self.connect(), _item_data(item, content_hash))
        self.connect().commit()

    def remove_items(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        placeholders = ",".join("?" for _ in keys)
        self.connect().execute(f"DELETE FROM items WHERE key IN ({placeholders})", keys)
        self.connect().commit()

    def apply_zotero_sync(
        self,
        items: Iterable[Tuple[ZoteroItem, Optional[str]]],
        removal_keys: Iterable[str],
        *,
        full: bool,
        revision: int,
        library_identity_sha256: str | None = None,
    ) -> SyncApplyResult:
        """Apply one complete Zotero revision and its watermark atomically.

        All network acquisition and validation must finish before this method is
        called. Full mode reconciles the local key set to the supplied active
        items; incremental mode removes only the supplied trash/tombstone keys.
        """

        item_data = [_item_data(item, content_hash) for item, content_hash in items]
        item_keys = [data[0] for data in item_data]
        if len(item_keys) != len(set(item_keys)):
            raise ValueError("Zotero sync batch contains duplicate active item keys")
        requested_removals = set(removal_keys)

        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing_rows = {
                row["key"]: row
                for row in conn.execute(
                    """
                    SELECT key, version, title, abstract, creators, tags, collections,
                           year, doi, url, raw_json, content_hash
                    FROM items
                    """
                )
            }
            if full:
                keys_to_remove = set(existing_rows) - set(item_keys)
            else:
                keys_to_remove = requested_removals

            # A tombstone or trash transition describes final absence and wins
            # over an item record for the same stable library revision.
            effective_item_data = [data for data in item_data if data[0] not in keys_to_remove]

            inserted = 0
            changed = 0
            for data in effective_item_data:
                existing = existing_rows.get(data[0])
                if existing is None:
                    inserted += 1
                    _execute_upsert(conn, data)
                    continue
                if _row_item_data(existing) != data:
                    if data[1] < existing["version"]:
                        raise ValueError(
                            f"Zotero item {data[0]} would regress from version "
                            f"{existing['version']} to {data[1]}"
                        )
                    changed += 1
                    _execute_upsert(conn, data)

            actually_present = set(existing_rows)
            actually_present.update(data[0] for data in effective_item_data)
            removed = len(actually_present & keys_to_remove)
            _delete_keys(conn, keys_to_remove)
            conn.execute(
                "REPLACE INTO metadata(key, value) VALUES(?, ?)",
                ("last_modified_version", str(revision)),
            )
            if library_identity_sha256 is not None:
                conn.execute(
                    "REPLACE INTO metadata(key, value) VALUES(?, ?)",
                    ("library_identity_sha256", library_identity_sha256),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        return SyncApplyResult(inserted=inserted, changed=changed, removed=removed)

    def set_embedding(self, key: str, vector: bytes) -> None:
        self.connect().execute(
            "UPDATE items SET embedding = ?, updated_at=CURRENT_TIMESTAMP WHERE key = ?",
            (vector, key),
        )
        self.connect().commit()

    def set_embeddings(self, embeddings: Iterable[Tuple[str, bytes]]) -> None:
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "UPDATE items SET embedding = ?, updated_at=CURRENT_TIMESTAMP WHERE key = ?",
                ((vector, key) for key, vector in embeddings),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def iter_items(self) -> Iterable[ZoteroItem]:
        cur = self.connect().execute("SELECT * FROM items")
        for row in cur:
            yield _row_to_item(row)

    def read_profile_snapshot(self) -> ProfileSnapshot:
        conn = self.connect()
        conn.execute("BEGIN")
        try:
            metadata = {
                row["key"]: row["value"]
                for row in conn.execute(
                    "SELECT key, value FROM metadata WHERE key IN (?, ?)",
                    ("last_modified_version", "library_identity_sha256"),
                )
            }
            revision_value = metadata.get("last_modified_version")
            identity = metadata.get("library_identity_sha256")
            if revision_value is None:
                raise ValueError("Committed Zotero library revision is missing")
            try:
                revision = int(revision_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("Committed Zotero library revision is invalid") from exc
            if revision < 0:
                raise ValueError("Committed Zotero library revision is invalid")
            if not identity:
                raise ValueError("Zotero library identity fingerprint is missing")
            items = tuple(
                _row_to_item(row)
                for row in conn.execute("SELECT * FROM items ORDER BY key ASC")
            )
            snapshot_sha256, input_set_sha256 = library_snapshot_fingerprints(items)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return ProfileSnapshot(
            library_identity_sha256=identity,
            revision=revision,
            items=items,
            snapshot_sha256=snapshot_sha256,
            embedding_input_set_sha256=input_set_sha256,
        )

    def fetch_items_without_embedding(self) -> List[Tuple[ZoteroItem, Optional[str]]]:
        cur = self.connect().execute(
            "SELECT * FROM items WHERE embedding IS NULL ORDER BY updated_at ASC"
        )
        rows = cur.fetchall()
        return [(_row_to_item(row), row["content_hash"]) for row in rows]

    def fetch_all_embeddings(self) -> List[Tuple[str, bytes]]:
        cur = self.connect().execute(
            "SELECT key, embedding FROM items WHERE embedding IS NOT NULL"
        )
        return [(row["key"], row["embedding"]) for row in cur]


def _row_to_item(row: sqlite3.Row) -> ZoteroItem:
    return ZoteroItem(
        key=row["key"],
        version=row["version"],
        title=row["title"],
        abstract=row["abstract"],
        creators=json.loads(row["creators"] or "[]"),
        tags=json.loads(row["tags"] or "[]"),
        collections=json.loads(row["collections"] or "[]"),
        year=row["year"],
        doi=row["doi"],
        url=row["url"],
        raw=json.loads(row["raw_json"]),
    )


def _item_data(item: ZoteroItem, content_hash: Optional[str]) -> Tuple[object, ...]:
    return (
        item.key,
        item.version,
        item.title,
        item.abstract,
        json.dumps(item.creators),
        json.dumps(item.tags),
        json.dumps(item.collections),
        item.year,
        item.doi,
        item.url,
        json.dumps(item.raw),
        content_hash,
    )


def _row_item_data(row: sqlite3.Row) -> Tuple[object, ...]:
    return (
        row["key"],
        row["version"],
        row["title"],
        row["abstract"],
        row["creators"],
        row["tags"],
        row["collections"],
        row["year"],
        row["doi"],
        row["url"],
        row["raw_json"],
        row["content_hash"],
    )


def _execute_upsert(conn: sqlite3.Connection, data: Sequence[object]) -> None:
    conn.execute(
        """
        INSERT INTO items(
            key, version, title, abstract, creators, tags, collections, year, doi, url, raw_json, content_hash
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            version=excluded.version,
            title=excluded.title,
            abstract=excluded.abstract,
            creators=excluded.creators,
            tags=excluded.tags,
            collections=excluded.collections,
            year=excluded.year,
            doi=excluded.doi,
            url=excluded.url,
            raw_json=excluded.raw_json,
            content_hash=excluded.content_hash,
            embedding=CASE
                WHEN items.content_hash IS excluded.content_hash THEN items.embedding
                ELSE NULL
            END,
            updated_at=CURRENT_TIMESTAMP
        """,
        data,
    )


def _delete_keys(conn: sqlite3.Connection, keys: Iterable[str]) -> None:
    ordered = sorted(set(keys))
    for offset in range(0, len(ordered), SQLITE_DELETE_BATCH_SIZE):
        batch = ordered[offset : offset + SQLITE_DELETE_BATCH_SIZE]
        placeholders = ",".join("?" for _ in batch)
        conn.execute(f"DELETE FROM items WHERE key IN ({placeholders})", batch)


__all__ = ["ProfileSnapshot", "ProfileStorage", "SyncApplyResult"]
