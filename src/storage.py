from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

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

    # item helpers
    def upsert_item(self, item: ZoteroItem, content_hash: Optional[str] = None) -> None:
        data = (
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
        self.connect().execute(
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
                updated_at=CURRENT_TIMESTAMP
            """,
            data,
        )
        self.connect().commit()

    def remove_items(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        placeholders = ",".join("?" for _ in keys)
        self.connect().execute(f"DELETE FROM items WHERE key IN ({placeholders})", keys)
        self.connect().commit()

    def set_embedding(self, key: str, vector: bytes) -> None:
        self.connect().execute(
            "UPDATE items SET embedding = ?, updated_at=CURRENT_TIMESTAMP WHERE key = ?",
            (vector, key),
        )
        self.connect().commit()

    def iter_items(self) -> Iterable[ZoteroItem]:
        cur = self.connect().execute("SELECT * FROM items")
        for row in cur:
            yield _row_to_item(row)

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


__all__ = ["ProfileStorage"]
