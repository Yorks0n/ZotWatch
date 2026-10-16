"""Read-only desktop Zotero → isolated P5B3 evaluation (never a production identity)."""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import ZoteroItem
from src.storage import ProfileStorage
from src.vectorizer import TextVectorizer
from zotwatch.interests.latent import LatentModelStore
from zotwatch.interests.latent_cli import evaluation_report
from zotwatch.interests.latent_contract import LatentParameters


def read_desktop_library(source, library_id):
    """Snapshot transaction; exclude trash, notes, annotations and attachments."""
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("BEGIN")
        binding = conn.execute("SELECT version FROM libraries WHERE libraryID=?", (library_id,)).fetchone()
        if binding is None:
            raise ValueError("Desktop library not found")
        rows = conn.execute("""
            SELECT i.key, i.version,
                MAX(CASE WHEN f.fieldName='title' THEN v.value END),
                MAX(CASE WHEN f.fieldName='abstractNote' THEN v.value END)
            FROM items i JOIN itemTypes t USING(itemTypeID)
            LEFT JOIN itemData d USING(itemID)
            LEFT JOIN fields f USING(fieldID)
            LEFT JOIN itemDataValues v USING(valueID)
            WHERE i.libraryID=? AND t.typeName NOT IN ('attachment','note','annotation')
              AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            GROUP BY i.key, i.version ORDER BY i.key
        """, (library_id,))
        items = {}
        for key, version, title, abstract in rows:
            items[key] = {"key": key, "version": max(0, version), "title": title or "", "abstract": abstract or None}
        return [ZoteroItem(**item) for item in items.values()], max(0, binding[0])


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zotero-db", required=True)
    parser.add_argument("--library-id", type=int, default=1)
    parser.add_argument("--evaluation-dir", required=True, help="New private directory, separate from every workspace")
    parser.add_argument("--min-cluster-size", type=int, default=10)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--merge-similarity", type=float, default=0.92)
    parser.add_argument("--selection", choices=["leaf", "eom"], default="leaf")
    args = parser.parse_args(arguments)
    destination = Path(args.evaluation_dir).resolve()
    # Do not overwrite a synced production mirror with a desktop-only identity.
    destination.mkdir(parents=True, exist_ok=False)
    items, revision = read_desktop_library(args.zotero_db, args.library_id)
    identity = sha256(f"desktop-evaluation:{Path(args.zotero_db).resolve()}:{args.library_id}".encode()).hexdigest()
    storage = ProfileStorage(destination / "profile.sqlite")
    try:
        storage.initialize()
        storage.apply_zotero_sync([(item, None) for item in items], [], full=True,
            revision=revision, library_identity_sha256=identity)
        update = LatentModelStore(destination).ensure(storage, TextVectorizer(),
            parameters=LatentParameters(min_cluster_size=args.min_cluster_size,
                min_samples=args.min_samples, merge_similarity=args.merge_similarity, selection=args.selection))
        report = destination / "evaluation.md"
        report.write_text(evaluation_report(update.model, items), encoding="utf-8")
        print(json.dumps({"model_revision": update.model.model_revision,
            "library_items": len(items), "centers": len(update.model.centers),
            "noise_items": len(update.model.noise_keys), "excluded_items": len(update.model.excluded_keys),
            "private_report": str(report)}))
    finally:
        storage.close()


if __name__ == "__main__":
    main()
