from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("hdbscan", reason="Install tests/requirements-latent.txt for the P5B3 gate")

from src.models import ZoteroItem
from zotwatch.interests.latent import LatentModelStore, cluster_vectors, make_centers, rebuild_reason, text_for_item, text_hashes
from zotwatch.interests.latent_contract import LatentModel, LatentParameters

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)
PARAMETERS = LatentParameters()


class Vectors:
    model_name = "synthetic-latent"
    dimension = 16
    artifact_identity = "synthetic-latent-v1"


def library(storage, count=60):
    for i in range(count):
        storage.upsert_item(ZoteroItem(key=f"K{i:04}", version=1, title=f"topic-{i // 20} paper-{i}",
            abstract="abstract evidence", creators=["PRIVATE_AUTHOR"], tags=["TAG_NOT_INPUT"]))
    storage.set_library_identity_sha256("a" * 64)
    storage.set_last_modified_version(1)
    return storage


def synthetic_encode(vectorizer, texts):
    result = []
    for text in texts:
        assert "PRIVATE_AUTHOR" not in text and "TAG_NOT_INPUT" not in text
        assert text.endswith("\nabstract evidence")
        number = int(text.split("paper-")[1].split()[0])
        row = np.random.default_rng(number).normal(0, 0.01, 16)
        row[int(text.split("topic-")[1].split()[0]) % 16] += 1
        result.append(row / np.linalg.norm(row))
    return np.asarray(result)


def build(store, storage, **kwargs):
    return store.ensure(storage, Vectors(), now=kwargs.pop("now", NOW), encode=synthetic_encode, **kwargs)


def test_real_hdbscan_noise_centroids_and_private_state(storage, tmp_path):
    library(storage)
    store = LatentModelStore(tmp_path)
    update = build(store, storage)
    assert update.rebuilt and update.encoded_items == 60 and update.reason == "initial"
    assert len(update.model.centers) == 3
    assert all(center.member_count == 20 and center.cohesion > 0.99 for center in update.model.centers)
    assert not update.model.noise_keys
    loaded, vectors = store.load()
    assert loaded == update.model and vectors.shape == (60, 16)
    assert all(center.semantic_refresh_required for center in loaded.centers)
    assert all("name" not in center.model_dump() and "description" not in center.model_dump() for center in loaded.centers)
    # Deliberately isolated directions remain outside all dense interests.
    keys = loaded.embedding_keys + [f"NOISE{i}" for i in range(8)]
    noise = np.eye(16)[3:11]
    vectors = np.concatenate([vectors, noise])
    groups = cluster_vectors(keys, vectors, PARAMETERS)
    assert len(groups) == 3
    assert {row for group in groups for row in group["rows"]} == set(range(60))


def test_complete_link_merge_does_not_chain_or_attach_noise():
    rng = np.random.default_rng(7)
    rows = []
    for angle in [0.0, 0.3, 0.6, 3.0]:
        center = np.array([np.cos(angle), np.sin(angle), 0.0, 0.0])
        group = center + rng.normal(0, 0.001, (20, 4))
        rows.extend(group / np.linalg.norm(group, axis=1, keepdims=True))
    rows.append([0, 0, 1, 0])
    vectors = np.asarray(rows)
    keys = [str(i).zfill(3) for i in range(len(rows))]
    groups = cluster_vectors(keys, vectors, PARAMETERS)
    assert sorted(len(group["rows"]) for group in groups) == [20, 20, 40]
    assert 80 not in {row for group in groups for row in group["rows"]}


def test_daily_reuse_and_monthly_accumulated_changes(storage, tmp_path):
    library(storage)
    store = LatentModelStore(tmp_path)
    first = build(store, storage)
    before = (tmp_path / "latent/current.json").read_bytes()
    item = next(storage.iter_items(ordered=True))
    # Tags/authors and Zotero version alone are not latent semantic changes.
    storage.upsert_item(item.model_copy(update={"tags": ["new tag"], "version": 2}))
    for day in (1, 29, 30, 60):
        result = build(store, storage, now=NOW + timedelta(days=day))
        assert not result.rebuilt and result.encoded_items == 0
        assert result.model.model_revision == first.model.model_revision
        assert (tmp_path / "latent/current.json").read_bytes() == before
    # 3/60 = 5%; collect changes against the old build, not the last no-op check.
    for i in range(60, 63):
        storage.upsert_item(ZoteroItem(key=f"K{i:04}", version=1,
            title=f"topic-0 paper-{i}", abstract="abstract evidence"))
    early = build(store, storage, now=NOW + timedelta(days=29))
    assert not early.rebuilt
    monthly = build(store, storage, now=NOW + timedelta(days=30))
    assert monthly.rebuilt and monthly.encoded_items == 3
    assert monthly.model.model_revision != first.model.model_revision
    assert {c.interest_id for c in monthly.model.centers} == {c.interest_id for c in first.model.centers}
    assert all(c.change == "stable" and not c.semantic_refresh_required for c in monthly.model.centers)


def test_title_only_is_excluded_before_embeddings_and_hdbscan(storage, tmp_path):
    library(storage)
    storage.upsert_item(ZoteroItem(key="TITLE_ONLY", version=1, title="title without abstract"))
    first = build(LatentModelStore(tmp_path), storage)
    assert first.encoded_items == 60
    assert first.model.excluded_keys == ["TITLE_ONLY"]
    assert "TITLE_ONLY" not in first.model.noise_keys
    assert "TITLE_ONLY" not in first.model.embedding_keys
    assert all("TITLE_ONLY" not in center.member_keys for center in first.model.centers)
    assert first.model.clustering_runtime.endswith("eligibility=abstract-required-v2")


def test_legacy_eligibility_policy_invalidates_reuse_without_losing_cached_vectors(storage, tmp_path):
    import json
    from hashlib import sha256
    library(storage)
    store = LatentModelStore(tmp_path)
    first = build(store, storage)
    legacy = first.model.model_copy(update={"clustering_runtime": first.model.clustering_runtime.split(";eligibility=")[0]})
    directory = store.root / "generations" / first.model.model_revision
    data = (legacy.model_dump_json() + "\n").encode()
    (directory / "model.json").write_bytes(data)
    (store.root / "current.json").write_text(json.dumps({"model_revision": legacy.model_revision, "model_sha256": sha256(data).hexdigest()}))
    updated = build(store, storage, now=NOW + timedelta(days=1))
    assert updated.rebuilt and updated.reason == "model_changed" and updated.encoded_items == 0
    assert {center.interest_id for center in updated.model.centers} == {center.interest_id for center in first.model.centers}


def test_gate_30_new_papers_edits_deletion_and_identity():
    hashes = {str(i): "a" * 64 for i in range(1000)}
    prior = SimpleNamespace(library_identity_sha256="a" * 64, embedding_fingerprint="b" * 64,
        parameters=PARAMETERS, clustering_runtime="test", built_at=NOW.isoformat(), item_text_hashes=hashes)
    def reason(current, day=30, **kwargs):
        return rebuild_reason(prior, current, kwargs.get("identity", "a" * 64),
            kwargs.get("fingerprint", "b" * 64), PARAMETERS, "test", NOW + timedelta(days=day), kwargs.get("manual", False))
    assert reason({**hashes, **{f"NEW{i}": "c" * 64 for i in range(29)}}) == "reuse_no_material_change"
    assert reason({**hashes, **{f"NEW{i}": "c" * 64 for i in range(30)}}) == "material_library_change"
    assert reason({k: v for k, v in hashes.items() if int(k) >= 50}) == "material_library_change"
    assert reason({k: "d" * 64 if int(k) < 50 else v for k, v in hashes.items()}) == "material_library_change"
    assert reason(hashes, day=1, manual=True) == "manual"
    assert reason(hashes, day=1, identity="c" * 64) == "library_changed"
    assert reason(hashes, day=1, fingerprint="c" * 64) == "model_changed"


def test_manual_rebuild_cached_embeddings_and_compatibility(storage, tmp_path):
    library(storage)
    store = LatentModelStore(tmp_path)
    first = build(store, storage)
    manual = build(store, storage, manual=True)
    assert manual.rebuilt and manual.encoded_items == 0
    assert [c.interest_id for c in manual.model.centers] == [c.interest_id for c in first.model.centers]
    storage.set_library_identity_sha256("c" * 64)
    changed = build(store, storage)
    assert changed.reason == "library_changed" and changed.encoded_items == 60
    assert not {c.interest_id for c in changed.model.centers} & {c.interest_id for c in first.model.centers}


def test_split_merge_and_large_composition_changes_request_semantic_refresh(storage, tmp_path):
    library(storage)
    old = build(LatentModelStore(tmp_path), storage).model
    keys = old.embedding_keys
    vectors = synthetic_encode(Vectors(), [text_for_item(i) for i in storage.iter_items(ordered=True)])
    hashes = text_hashes(storage.iter_items())
    groups = [{"rows": list(range(10)), "stability": 0.8},
              {"rows": list(range(10, 20)), "stability": 0.8},
              {"rows": list(range(20, 60)), "stability": 0.8}]
    result = make_centers(keys, vectors, groups, "b" * 32, PARAMETERS, old, hashes)
    assert [c.change for c in result] == ["split", "split", "merged"]
    assert all(c.semantic_refresh_required for c in result)
    assert not {c.interest_id for c in result} & {c.interest_id for c in old.centers}
    assert len(result[2].previous_interest_ids) == 2
    changed_hashes = {key: "e" * 64 for key in hashes}
    groups = [{"rows": list(range(i, i + 20)), "stability": 0.8} for i in (0, 20, 40)]
    result = make_centers(keys, vectors, groups, "b" * 32, PARAMETERS, old, changed_hashes)
    assert all(c.change == "changed" and c.semantic_refresh_required for c in result)
    assert [c.interest_id for c in result] == [c.interest_id for c in old.centers]


def test_failed_build_and_library_drift_keep_previous_revision(storage, tmp_path):
    library(storage)
    store = LatentModelStore(tmp_path)
    first = build(store, storage)
    pointer = (tmp_path / "latent/current.json").read_bytes()
    def drift(keys, vectors, parameters):
        storage.set_last_modified_version(2)
        return cluster_vectors(keys, vectors, parameters)
    with pytest.raises(ValueError, match="Library changed"):
        build(store, storage, manual=True, cluster=drift)
    assert (tmp_path / "latent/current.json").read_bytes() == pointer
    assert store.load()[0] == first.model
    assert len(list((tmp_path / "latent/generations").iterdir())) == 1


def test_corruption_unknown_schema_and_invalid_vectors_fail_closed(storage, tmp_path):
    library(storage)
    store = LatentModelStore(tmp_path)
    model = build(store, storage).model
    data = model.model_dump()
    data["schema_version"] = 2
    with pytest.raises(ValueError):
        LatentModel.model_validate(data)
    data = model.model_dump()
    data["noise_keys"] = model.centers[0].member_keys
    with pytest.raises(ValueError, match="partition"):
        LatentModel.model_validate(data)
    file = tmp_path / "latent/generations" / model.model_revision / "embeddings.npy"
    file.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        build(store, storage, manual=True)
    other = LatentModelStore(tmp_path / "other")
    with pytest.raises(ValueError, match="embedding"):
        other.ensure(storage, Vectors(), now=NOW, encode=lambda *_: np.full((60, 16), np.nan))
    assert not (tmp_path / "other/latent/current.json").exists()


def test_sparse_empty_and_title_abstract_only(storage, tmp_path):
    library(storage, 2)
    storage.upsert_item(ZoteroItem(key="EMPTY", version=1, title="", tags=["some tag"], creators=["some author"]))
    store = LatentModelStore(tmp_path)
    model = build(store, storage).model
    assert not model.centers and len(model.noise_keys) == 2 and model.excluded_keys == ["EMPTY"]
    assert text_for_item(ZoteroItem(key="A", version=1, title=" title ", abstract=" abstract ", tags=["tag"])) == "title\nabstract"
    storage.remove_items([item.key for item in storage.iter_items()])
    model = build(store, storage, manual=True).model
    assert not model.centers and not model.embedding_keys and not model.noise_keys
    assert store.load()[1].shape == (0, 0)


def test_cli_counts_private_report_and_missing_library(storage, tmp_path, monkeypatch, capsys):
    from zotwatch.cli import main
    from src import vectorizer
    from zotwatch.interests import latent
    # CLI uses the actual persistence/lease path, with only the model boundary replaced.
    library(storage)
    monkeypatch.setattr(vectorizer, "TextVectorizer", Vectors)
    real_ensure = latent.LatentModelStore.ensure
    def ensure(self, *args, **kwargs):
        kwargs["encode"] = synthetic_encode
        return real_ensure(self, *args, **kwargs)
    monkeypatch.setattr(latent.LatentModelStore, "ensure", ensure)
    assert main(["interests", "build", "--workspace", str(tmp_path), "--state-dir", str(storage.path.parent), "--report"]) == 0
    output = capsys.readouterr().out
    assert '"centers": 3' in output and "PRIVATE_AUTHOR" not in output and "paper-" not in output
    import json
    report = json.loads(output)["private_report"]
    from pathlib import Path
    assert "paper-" in Path(report).read_text()
    assert main(["interests", "inspect", "--state-dir", str(storage.path.parent)]) == 0
    assert main(["interests", "build", "--workspace", str(tmp_path / "missing")]) == 4
    assert "SYNCED_LIBRARY_REQUIRED" in capsys.readouterr().out


def test_desktop_snapshot_is_read_only_and_excludes_private_nonpapers(tmp_path):
    import importlib.util
    import sqlite3
    from .helpers import ROOT
    spec = importlib.util.spec_from_file_location("desktop_latent", ROOT / "scripts/evaluate_latent_zotero.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "zotero.sqlite"
    with sqlite3.connect(source) as conn:
        conn.executescript("""
          CREATE TABLE libraries(libraryID INTEGER, version INTEGER);
          CREATE TABLE items(itemID INTEGER, itemTypeID INTEGER, libraryID INTEGER, key TEXT, version INTEGER);
          CREATE TABLE itemTypes(itemTypeID INTEGER, typeName TEXT);
          CREATE TABLE itemData(itemID INTEGER, fieldID INTEGER, valueID INTEGER);
          CREATE TABLE fields(fieldID INTEGER, fieldName TEXT);
          CREATE TABLE itemDataValues(valueID INTEGER, value TEXT);
          CREATE TABLE deletedItems(itemID INTEGER);
          INSERT INTO libraries VALUES(1,17);
          INSERT INTO itemTypes VALUES(1,'journalArticle'),(2,'attachment'),(3,'note'),(4,'annotation');
          INSERT INTO items VALUES(1,1,1,'PAPER',3),(2,2,1,'PDF',2),(3,3,1,'NOTE',2),
            (4,4,1,'ANNOTATION',2),(5,1,1,'TRASH',2),(6,1,2,'OTHER_LIBRARY',2);
          INSERT INTO deletedItems VALUES(5);
          INSERT INTO fields VALUES(1,'title'),(2,'abstractNote'),(3,'extra');
          INSERT INTO itemData VALUES(1,1,1),(1,2,2),(1,3,3),(2,1,3);
          INSERT INTO itemDataValues VALUES(1,'Paper title'),(2,'Paper abstract'),(3,'PRIVATE_NOT_INPUT');
        """)
    before = source.read_bytes()
    items, revision = module.read_desktop_library(source, 1)
    assert source.read_bytes() == before
    assert revision == 17 and len(items) == 1
    assert text_for_item(items[0]) == "Paper title\nPaper abstract"
    assert items[0].key == "PAPER" and not items[0].raw
    assert "PRIVATE_NOT_INPUT" not in items[0].model_dump_json()
