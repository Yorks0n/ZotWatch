from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict


def json_dumps(data: Any, *, indent: int | None = None) -> str:
    return json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True)


def hash_content(*parts: str) -> str:
    sha = hashlib.sha256()
    for part in parts:
        if part:
            sha.update(part.encode("utf-8"))
    return sha.hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def iso_to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def chunk_dict(d: Dict[str, Any], *, max_len: int = 80) -> Dict[str, Any]:
    """Split long string values to keep JSON manageable (best-effort)."""
    result = {}
    for key, value in d.items():
        if isinstance(value, str) and len(value) > max_len:
            result[key] = value[:max_len] + "…"
        else:
            result[key] = value
    return result


__all__ = ["hash_content", "json_dumps", "utc_now", "ensure_isoformat", "iso_to_datetime"]
