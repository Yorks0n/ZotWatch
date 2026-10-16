"""P5B3 latent model building and monthly reuse, under the existing state lease."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import shutil
from uuid import uuid4

import numpy as np

from src.computational_state import StateCoordinator, descriptor_for_vectorizer
from .latent_contract import LatentCenter, LatentModel, LatentParameters
from .interest_formation import ELIGIBILITY_POLICY_VERSION
from .ranking import encode_texts

TEXT_POLICY = "title-abstract-token-windows-128-mean-v1"


def text_for_item(item):
    # E4 embeddings also contain creators/tags and must never be reused here.
    return "\n".join(part.strip() for part in (item.title, item.abstract or "") if part.strip())


def text_hashes(items):
    return {item.key: sha256(text_for_item(item).encode()).hexdigest() for item in items}


def rebuild_reason(previous, hashes, identity, fingerprint, parameters, runtime, now, manual=False):
    if previous is None:
        return "initial"
    if previous.library_identity_sha256 != identity:
        return "library_changed"
    if previous.embedding_fingerprint != fingerprint or previous.parameters != parameters or previous.clustering_runtime != runtime:
        return "model_changed"
    if manual:
        return "manual"
    if now < datetime.fromisoformat(previous.built_at) + timedelta(days=30):
        return "reuse_before_monthly_check"
    old, current = set(previous.item_text_hashes), set(hashes)
    added, deleted = current - old, old - current
    edited = {key for key in old & current if hashes[key] != previous.item_text_hashes[key]}
    if len(added) >= 30 or len(added | deleted | edited) / max(1, len(old)) >= 0.05:
        return "material_library_change"
    return "reuse_no_material_change"


def _unit(row):
    row = np.asarray(row, dtype=np.float64)
    norm = np.linalg.norm(row)
    if not np.isfinite(row).all() or norm <= 1e-12:
        raise ValueError("Invalid latent embedding")
    return row / norm


def cluster_vectors(keys, vectors, parameters):
    """HDBSCAN noise stays noise; complete-link center merging avoids bridge chains."""
    from hdbscan import HDBSCAN
    if len(keys) < max(parameters.min_cluster_size, parameters.min_samples + 1):
        return []
    fitted = HDBSCAN(min_cluster_size=parameters.min_cluster_size,
        min_samples=parameters.min_samples, metric="euclidean",
        cluster_selection_method=parameters.selection, core_dist_n_jobs=1,
        approx_min_span_tree=False, allow_single_cluster=False).fit(vectors)
    groups = []
    for label in sorted(set(fitted.labels_) - {-1}):
        rows = np.flatnonzero((fitted.labels_ == label) & (fitted.probabilities_ >= parameters.min_membership)).tolist()
        if len(rows) >= parameters.min_cluster_size:
            groups.append({"rows": rows, "stability": float(fitted.cluster_persistence_[label]),
                           "parts": [_unit(vectors[rows].mean(axis=0))]})
    # Only original cluster centers participate in the complete-link check.
    while True:
        choices = []
        for i, left in enumerate(groups):
            for j in range(i + 1, len(groups)):
                right = groups[j]
                similarity = min(float(a @ b) for a in left["parts"] for b in right["parts"])
                if similarity >= parameters.merge_similarity:
                    choices.append((similarity, -i, -j))
        if not choices:
            break
        _, ni, nj = max(choices)
        i, j = -ni, -nj
        left, right = groups[i], groups[j]
        weight = len(left["rows"]) + len(right["rows"])
        groups[i] = {"rows": sorted(left["rows"] + right["rows"]),
            "parts": left["parts"] + right["parts"],
            "stability": (left["stability"] * len(left["rows"]) + right["stability"] * len(right["rows"])) / weight}
        groups.pop(j)
    return sorted(groups, key=lambda group: min(keys[row] for row in group["rows"]))


def make_centers(keys, vectors, groups, revision, parameters, previous, hashes):
    centers = []
    for group in groups:
        rows = group["rows"]
        centroid = _unit(vectors[rows].mean(axis=0))
        similarities = np.clip(vectors[rows] @ centroid, -1, 1)
        representatives = sorted(zip(rows, similarities), key=lambda pair: (-pair[1], keys[pair[0]]))
        centers.append(dict(centroid=centroid.tolist(), member_keys=sorted(keys[row] for row in rows),
            member_count=len(rows), cohesion=float(np.clip(similarities.mean(), 0, 1)),
            stability=float(np.clip(group["stability"], 0, 1)),
            representative_items=[keys[row] for row, _ in representatives[:parameters.representative_count]],
            model_revision=revision))
    old = previous.centers if previous else []
    # Meaningful overlap graph detects splits/merges before assigning any old ID.
    links = []
    for center in centers:
        members = set(center["member_keys"])
        links.append([i for i, prior in enumerate(old)
                      if len(members & set(prior.member_keys)) / min(len(members), prior.member_count) >= 0.25])
    degrees = {i: sum(i in parents for parents in links) for i in range(len(old))}
    result = []
    for center, parents in zip(centers, links):
        interest_id, change, refresh = str(uuid4()), "new", True
        if len(parents) > 1:
            change = "reorganized" if any(degrees[i] > 1 for i in parents) else "merged"
        elif len(parents) == 1:
            prior = old[parents[0]]
            if degrees[parents[0]] > 1:
                change = "split"
            else:
                members, prior_members = set(center["member_keys"]), set(prior.member_keys)
                overlap = len(members & prior_members) / len(members | prior_members)
                similarity = float(np.asarray(center["centroid"]) @ prior.centroid)
                if overlap >= 0.5 and similarity >= 0.85:
                    interest_id = prior.interest_id
                    edited = sum(hashes[k] != previous.item_text_hashes[k] for k in members & prior_members)
                    refresh = overlap < 0.8 or similarity < 0.95 or edited / len(members) >= 0.2
                    change = "changed" if refresh else "stable"
                else:
                    change = "changed"
        result.append(LatentCenter(**center, interest_id=interest_id, change=change,
            previous_interest_ids=[old[i].interest_id for i in parents], semantic_refresh_required=refresh))
    return result


@dataclass(frozen=True)
class LatentUpdate:
    model: LatentModel
    rebuilt: bool
    reason: str
    encoded_items: int


class LatentModelStore:
    def __init__(self, state_dir):
        self.state = Path(state_dir).resolve()
        self.root = self.state / "latent"

    def load(self):
        pointer = self.root / "current.json"
        if not pointer.exists():
            return None, None
        from pydantic import Field
        from .latent_contract import Closed, REVISION, SHA256
        class Pointer(Closed):
            model_revision: str = Field(pattern=REVISION)
            model_sha256: str = Field(pattern=SHA256)
        current = Pointer.model_validate_json(pointer.read_bytes())
        directory = self.root / "generations" / current.model_revision
        data = (directory / "model.json").read_bytes()
        if sha256(data).hexdigest() != current.model_sha256:
            raise ValueError("Latent model checksum mismatch")
        model = LatentModel.model_validate_json(data)
        if model.model_revision != current.model_revision:
            raise ValueError("Latent pointer revision mismatch")
        file = directory / "embeddings.npy"
        if sha256(file.read_bytes()).hexdigest() != model.embeddings_sha256:
            raise ValueError("Latent embedding checksum mismatch")
        vectors = np.load(file, allow_pickle=False)
        if vectors.ndim != 2 or vectors.shape[0] != len(model.embedding_keys) or not np.isfinite(vectors).all():
            raise ValueError("Invalid latent embedding matrix")
        if len(vectors) and not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-6):
            raise ValueError("Latent embeddings must be unit vectors")
        if any(len(center.centroid) != vectors.shape[1] for center in model.centers):
            raise ValueError("Latent embedding dimension mismatch")
        return model, vectors

    def ensure(self, storage, vectorizer, *, parameters=None, manual=False, now=None,
               encode=encode_texts, cluster=cluster_vectors, lease=None):
        if lease is None:
            with StateCoordinator(self.state).acquire() as owned:
                return self.ensure(storage, vectorizer, parameters=parameters, manual=manual,
                    now=now, encode=encode, cluster=cluster, lease=owned)
        lease.validate_for(self.state)
        parameters = parameters or LatentParameters()
        now = now or datetime.now(timezone.utc)
        if now.utcoffset() is None:
            raise ValueError("Model check requires a timezone")
        snapshot = storage.read_profile_snapshot()
        items = sorted(snapshot.items, key=lambda item: item.key)
        if len(items) > 20000:
            raise ValueError("Latent evaluation supports at most 20000 items")
        hashes = text_hashes(items)
        embedding_contract = descriptor_for_vectorizer(vectorizer).compatibility_payload()
        embedding_contract["input_schema"] = "title-abstract-v1"
        embedding_contract["text_policy"] = TEXT_POLICY
        fingerprint = sha256(json.dumps(embedding_contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        runtime = f"hdbscan:{version('hdbscan')};sklearn:{version('scikit-learn')};numpy:{version('numpy')};eligibility={ELIGIBILITY_POLICY_VERSION}"
        previous, old_vectors = self.load()
        reason = rebuild_reason(previous, hashes, snapshot.library_identity_sha256,
            fingerprint, parameters, runtime, now, manual)
        if reason.startswith("reuse_"):
            return LatentUpdate(previous, False, reason, 0)
        compatible = previous is not None and previous.library_identity_sha256 == snapshot.library_identity_sha256 and previous.embedding_fingerprint == fingerprint
        cached = dict(zip(previous.embedding_keys, old_vectors)) if compatible else {}
        eligible = [item for item in items if (item.abstract or "").strip()]
        new_items = [item for item in eligible if item.key not in cached or hashes[item.key] != previous.item_text_hashes[item.key]]
        if new_items:
            encoded = np.asarray(encode(vectorizer, [text_for_item(item) for item in new_items]), dtype=np.float64)
            if encoded.ndim != 2 or len(encoded) != len(new_items):
                raise ValueError("Invalid latent encoding result")
            cached.update((item.key, _unit(row)) for item, row in zip(new_items, encoded))
        keys = [item.key for item in eligible]
        vectors = np.asarray([cached[key] for key in keys], dtype=np.float64) if keys else np.empty((0, 0))
        groups = cluster(keys, vectors, parameters) if keys else []
        revision = uuid4().hex
        centers = make_centers(keys, vectors, groups, revision, parameters, previous if compatible else None, hashes)
        members = {key for center in centers for key in center.member_keys}
        directory = self.root / "generations" / revision
        staging = directory.with_name("." + revision + ".tmp")
        staging.mkdir(parents=True)
        try:
            np.save(staging / "embeddings.npy", vectors, allow_pickle=False)
            model = LatentModel(model_revision=revision, built_at=now.isoformat(),
                library_identity_sha256=snapshot.library_identity_sha256, library_revision=snapshot.revision,
                embedding_fingerprint=fingerprint, clustering_runtime=runtime, parameters=parameters,
                item_text_hashes=hashes, embedding_keys=keys,
                embeddings_sha256=sha256((staging / "embeddings.npy").read_bytes()).hexdigest(),
                noise_keys=sorted(set(keys) - members), excluded_keys=sorted(set(hashes) - set(keys)), centers=centers)
            data = (model.model_dump_json() + "\n").encode()
            (staging / "model.json").write_bytes(data)
            current = storage.read_profile_snapshot()
            if (current.library_identity_sha256, current.revision, text_hashes(current.items)) != (snapshot.library_identity_sha256, snapshot.revision, hashes):
                raise ValueError("Library changed during latent build")
            lease.validate_for(self.state)
            staging.replace(directory)
            temporary = self.root / "current.tmp"
            temporary.write_text(json.dumps({"model_revision": revision, "model_sha256": sha256(data).hexdigest()}) + "\n")
            temporary.replace(self.root / "current.json")
            return LatentUpdate(model, True, reason, len(new_items))
        finally:
            if staging.exists():
                shutil.rmtree(staging)
