"""Latest-only private runner cache. No run results, credentials, or checkpoints."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

from src.computational_state import pseudonymous_library_identity

FILES = ("metadata-cache-v1.json", "center-profiles-v1.json", "public-feed.xml")
AI_FILES = ("translation-cache-v1.json", "quality-llm-cache-v1.json", "jev-cache-v1.json")


def copy_file(source: Path, destination: Path):
    if source.is_symlink() or not source.is_file():
        raise ValueError("Invalid cache file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def current_generation(source: Path, destination: Path, folder: str, pointer: str, key: str, names):
    current = source / folder / pointer
    if not current.exists():
        return
    value = json.loads(current.read_bytes())
    revision = value[key]
    if not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", revision):
        raise ValueError("Invalid cache generation")
    copy_file(current, destination / folder / pointer)
    for name in names:
        relative = Path(folder) / "generations" / revision / name
        copy_file(source / relative, destination / relative)
    if folder == "latent-state-v1":
        # Only the active deployment is needed by the daily runner, not its lineage.
        revision = value["deployment_revision"]
        if not re.fullmatch(r"[a-f0-9]{32}", revision):
            raise ValueError("Invalid deployment revision")
        relative = Path(folder) / "deployments" / f"{revision}.json"
        copy_file(source / relative, destination / relative)


def snapshot(source: Path, destination: Path, repository_id: str, library_id: str):
    """Copy a closed set of current working state; SQLite is backed up consistently."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    database = source / "profile.sqlite"
    if database.is_symlink() or not database.is_file():
        raise ValueError("Missing SQLite state")
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("Invalid SQLite state")
        with sqlite3.connect(destination / "profile.sqlite") as backup:
            db.backup(backup)
    current_generation(source, destination, "computational", "current.json", "generation_id",
                       ("state-manifest.json", "profile.json", "embeddings.npz", "faiss.index"))
    current_generation(source, destination, "latent-state-v1", "current.json", "interest_model_revision",
                       ("model.json", "generation.json", "embeddings.npy"))
    for name in FILES:
        if (source / name).exists():
            copy_file(source / name, destination / name)
    for name in AI_FILES:
        relative = Path("optional-ai-cache-v1") / name
        if (source / relative).exists():
            copy_file(source / relative, destination / relative)
    (destination / "cache.json").write_text(json.dumps({
        "version": 1, "repository_id": repository_id,
        "library_identity": pseudonymous_library_identity("user", library_id),
    }))


def restore(source: Path, destination: Path, repository_id: str, library_id: str):
    try:
        identity = json.loads((source / "cache.json").read_bytes())
        if identity != {"version": 1, "repository_id": repository_id,
                        "library_identity": pseudonymous_library_identity("user", library_id)}:
            raise ValueError("Different cache scope")
        snapshot(source, destination, repository_id, library_id)
        return True
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        shutil.rmtree(destination, ignore_errors=True)
        destination.mkdir(parents=True, exist_ok=True)
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("restore", "save"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY_ID"]
    library = os.environ["ZOTERO_USER_ID"]
    if args.operation == "restore":
        print(json.dumps({"restored": restore(args.cache, args.state, repository, library)}))
    else:
        snapshot(args.state, args.cache, repository, library)
        print(json.dumps({"saved": True}))


if __name__ == "__main__":
    main()
