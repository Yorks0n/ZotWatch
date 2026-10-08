"""P5C4 opt-in private per-library accepted formation and deployment authority."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import numpy as np
from pydantic import Field, model_validator

from src.computational_state import StateCoordinator
from .center_recall_contract import (Closed, EncoderIdentity, CenterRecallModel,
    FormalCentroid, canonical, digest, save_center_model, load_center_model, SHA256)
from .center_recall import encode_candidates
from .interest_formation import form_interest_model
from .latent import rebuild_reason, make_centers
from .latent_contract import LatentCenter, LatentParameters

FORMATION_POLICY = "auto-spherical-size-v1/abstract-required-v2/membership-qualification-experimental-v1/trim-once"
REVISION = r"^[0-9a-f]{32}$"


def accepted_encoder():
    return EncoderIdentity.model_validate_json((Path(__file__).parents[1] / "resources/latent-encoder-v1.json").read_bytes())


def validate_encoder(cache, identity=None):
    identity = identity or accepted_encoder()
    if identity != accepted_encoder():
        raise ValueError("Encoder incompatible with accepted policy")
    snapshot = Path(cache) / ("models--" + identity.model_identifier.replace("/", "--")) / "snapshots" / identity.model_revision
    for name, checksum in identity.artifact_sha256.items():
        if sha256((snapshot / name).read_bytes()).hexdigest() != checksum:
            raise ValueError("Encoder checksum mismatch")


class Deployment(Closed):
    schema_name: Literal["zotwatch-latent-deployment"] = "zotwatch-latent-deployment"
    schema_version: Literal[1] = 1
    deployment_revision: str = Field(pattern=REVISION)
    previous_deployment_revision: str | None = Field(default=None, pattern=REVISION)
    workspace_repository_id: int = Field(gt=0)
    library_identity_sha256: str = Field(pattern=SHA256)
    library_revision: int = Field(ge=0)
    input_sha256: str = Field(pattern=SHA256)
    interest_model_revision: str = Field(pattern=REVISION)
    model_sha256: str = Field(pattern=SHA256)
    generation_sha256: str = Field(pattern=SHA256)
    formation_policy: Literal[FORMATION_POLICY] = FORMATION_POLICY
    encoder: EncoderIdentity
    embedding_text_fingerprint: str = Field(pattern=SHA256)
    activated_at: str
    activation_reason: Literal["initial", "library_changed", "model_changed", "manual", "material_library_change", "rollback"]

    @model_validator(mode="after")
    def compatible(self):
        if self.encoder != accepted_encoder() or self.embedding_text_fingerprint != digest(self.encoder.model_dump()):
            raise ValueError("Deployment encoder mismatch")
        if datetime.fromisoformat(self.activated_at).utcoffset() is None:
            raise ValueError("Deployment timestamp requires timezone")
        return self


class InputRecord(Closed):
    key: str = Field(min_length=1)
    title: str
    abstract: str | None
    doi: str | None


class Generation(Closed):
    schema_name: Literal["zotwatch-latent-generation"] = "zotwatch-latent-generation"
    schema_version: Literal[1] = 1
    model_revision: str = Field(pattern=REVISION)
    workspace_repository_id: int = Field(gt=0)
    library_identity_sha256: str = Field(pattern=SHA256)
    library_revision: int = Field(ge=0)
    input_sha256: str = Field(pattern=SHA256)
    input_records: list[InputRecord] = Field(max_length=20000)
    built_at: str
    formation_policy: Literal[FORMATION_POLICY] = FORMATION_POLICY
    embedding_fingerprint: str = Field(pattern=SHA256)
    clustering_runtime: str
    item_text_hashes: dict[str, str]
    embedding_keys: list[str]
    embeddings_sha256: str = Field(pattern=SHA256)
    model_sha256: str = Field(pattern=SHA256)
    previous_model_revision: str | None = Field(default=None, pattern=REVISION)
    centers: list[LatentCenter] = Field(max_length=60)
    formation: dict

    @model_validator(mode="after")
    def coherent(self):
        records = [r.model_dump() for r in self.input_records]
        if len({r.key for r in self.input_records}) != len(records) or digest(records) != self.input_sha256:
            raise ValueError("Generation input snapshot mismatch")
        if self.item_text_hashes != {r["key"]: digest(r) for r in records}:
            raise ValueError("Generation input hashes mismatch")
        if self.embedding_keys != [r["key"] for r in records if (r["abstract"] or "").strip()]:
            raise ValueError("Generation embedding alignment mismatch")
        groups = [g for g in self.formation["qualification"]["clusters"] if g["published"]]
        if len(groups) != len(self.centers):
            raise ValueError("Generation formal centers mismatch")
        for c, g in zip(self.centers, groups):
            if (c.model_revision != self.model_revision or c.centroid != g["trimmed_centroid"] or
                g["distinct_strong_works"] < 10 or c.member_keys != sorted(self.embedding_keys[i] for i in g["probe_strong_rows"])):
                raise ValueError("Generation qualified center mismatch")
        if datetime.fromisoformat(self.built_at).utcoffset() is None:
            raise ValueError("Generation timestamp requires timezone")
        return self


def records_for(snapshot):
    records = []
    for item in sorted(snapshot.items, key=lambda i: i.key):
        raw = item.raw.get("data", {})
        if raw.get("itemType") in {"attachment", "note", "annotation"} or raw.get("deleted") in (True, 1):
            continue
        records.append(dict(key=item.key, title=item.title, abstract=item.abstract, doi=item.doi))
    return records


class LifecycleStore:
    def __init__(self, state):
        self.state = Path(state).resolve()
        self.root = self.state / "latent-state-v1"

    def token(self):
        p = self.root / "current.json"
        return sha256(p.read_bytes()).hexdigest() if p.exists() else None

    def current(self):
        p = self.root / "current.json"
        if not p.exists():
            return None
        if p.is_symlink():
            raise ValueError("Unsafe latent current pointer")
        deployment = Deployment.model_validate_json(p.read_bytes())
        immutable = self.root / "deployments" / (deployment.deployment_revision + ".json")
        if immutable.is_symlink() or Deployment.model_validate_json(immutable.read_bytes()) != deployment:
            raise ValueError("Current deployment immutable copy mismatch")
        return deployment

    def load(self, deployment, repository_id, library_identity):
        if deployment.workspace_repository_id != repository_id or deployment.library_identity_sha256 != library_identity:
            raise ValueError("Latent deployment ownership mismatch")
        folder = self.root / "generations" / deployment.interest_model_revision
        if folder.is_symlink() or any((folder / name).is_symlink() for name in ("generation.json", "model.json", "embeddings.npy")):
            raise ValueError("Unsafe latent generation")
        raw = (folder / "generation.json").read_bytes()
        if sha256(raw).hexdigest() != deployment.generation_sha256:
            raise ValueError("Generation checksum mismatch")
        generation = Generation.model_validate_json(raw)
        if (generation.model_revision, generation.workspace_repository_id, generation.library_identity_sha256,
            generation.library_revision, generation.input_sha256, generation.formation_policy, generation.embedding_fingerprint,
            generation.model_sha256) != (deployment.interest_model_revision, repository_id, library_identity,
            deployment.library_revision, deployment.input_sha256, deployment.formation_policy, deployment.embedding_text_fingerprint,
            deployment.model_sha256):
            raise ValueError("Generation deployment mismatch")
        path = folder / "model.json"
        if sha256(path.read_bytes()).hexdigest() != deployment.model_sha256:
            raise ValueError("Deployment model checksum mismatch")
        model = load_center_model(path)
        if (model.interest_model_revision, model.source_snapshot_sha256, model.encoder, model.embedding_text_fingerprint,
            [(c.interest_id, c.centroid) for c in model.centers], model.threshold) != (
            generation.model_revision, generation.input_sha256, deployment.encoder, generation.embedding_fingerprint,
            [(c.interest_id, c.centroid) for c in generation.centers], .55):
            raise ValueError("Runtime model revision mismatch")
        data = (folder / "embeddings.npy").read_bytes()
        if sha256(data).hexdigest() != generation.embeddings_sha256:
            raise ValueError("Generation vector checksum mismatch")
        vectors = np.load(folder / "embeddings.npy", allow_pickle=False)
        if vectors.shape != (len(generation.embedding_keys), 384) or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1., atol=1e-6):
            raise ValueError("Generation vector alignment mismatch")
        return generation, vectors, path

    def _switch(self, deployment, expected, lease):
        lease.validate_for(self.state)
        if self.token() != expected:
            raise ValueError("Latent current CAS conflict")
        directory = self.root / "deployments"
        directory.mkdir(parents=True, exist_ok=True)
        data = canonical(deployment.model_dump())
        with (directory / (deployment.deployment_revision + ".json")).open("xb") as f:
            f.write(data)
        temp = self.root / (".current-" + uuid4().hex)
        try:
            temp.write_bytes(data)
            lease.validate_for(self.state)
            if self.token() != expected:
                raise ValueError("Latent current CAS conflict")
            temp.replace(self.root / "current.json")
        finally:
            temp.unlink(missing_ok=True)

    def ensure(self, storage, repository_id, library_identity, cache, *, manual=False, now=None,
               encode=None, lease=None):
        if lease is None:
            with StateCoordinator(self.state).acquire() as owned:
                return self.ensure(storage, repository_id, library_identity, cache, manual=manual, now=now, encode=encode, lease=owned)
        lease.validate_for(self.state)
        now = now or datetime.now(timezone.utc)
        if now.utcoffset() is None or not isinstance(repository_id, int) or repository_id <= 0:
            raise ValueError("Verified private workspace and timestamp required")
        encode = encode or encode_candidates
        snapshot = storage.read_profile_snapshot()
        if snapshot.library_identity_sha256 != library_identity:
            raise ValueError("Verified library snapshot mismatch")
        records = records_for(snapshot)
        if len(records) > 20000:
            raise ValueError("Latent input budget exceeded")
        hashes = {r["key"]: digest(r) for r in records}
        encoder = accepted_encoder()
        try:
            validate_encoder(cache, encoder)
        except (OSError, ValueError):
            from .recall_integration import LatentRecallUnavailable
            raise LatentRecallUnavailable("LATENT_RECALL_ENCODER_UNAVAILABLE") from None
        fingerprint = digest(encoder.model_dump())
        runtime = f"sklearn:{version('scikit-learn')};numpy:{version('numpy')};{FORMATION_POLICY};input=paper-records-v1"
        expected, prior = self.token(), self.current()
        previous, old_vectors, _ = self.load(prior, repository_id, prior.library_identity_sha256) if prior else (None, None, None)
        compatible = previous is not None and previous.library_identity_sha256 == library_identity and previous.embedding_fingerprint == fingerprint
        gate = SimpleNamespace(**previous.model_dump(), parameters=FORMATION_POLICY) if previous else None
        reason = rebuild_reason(gate, hashes, library_identity, fingerprint, FORMATION_POLICY, runtime, now, manual)
        if reason.startswith("reuse_"):
            return prior, reason, 0
        cached = dict(zip(previous.embedding_keys, old_vectors)) if compatible else {}
        eligible = [r for r in records if (r["abstract"] or "").strip()]
        changed = [r for r in eligible if r["key"] not in cached or hashes[r["key"]] != previous.item_text_hashes[r["key"]]]
        if changed:
            shell = CenterRecallModel(interest_model_revision=uuid4().hex, source_snapshot_sha256=digest(records),
                encoder=encoder, embedding_text_fingerprint=fingerprint, threshold=.55, centers=[])
            # The accepted runtime encoder has a 2,000-candidate call budget;
            # library formation retains its independent 20,000-record budget.
            from .center_recall import MAX_CANDIDATES
            for start in range(0, len(changed), MAX_CANDIDATES):
                batch = changed[start:start + MAX_CANDIDATES]
                x, meta = encode(shell, cache, [dict(id=r["key"], title=r["title"], abstract=r["abstract"]) for r in batch])
                if np.asarray(x).shape != (len(batch), 384) or meta["embedding_text_fingerprint"] != fingerprint:
                    raise ValueError("Bootstrap encoding fingerprint/dimension mismatch")
                cached.update((r["key"], row) for r, row in zip(batch, x))
        vectors = np.asarray([cached[r["key"]] for r in eligible], dtype=np.float64).reshape(-1, 384)
        if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1., atol=1e-6):
            raise ValueError("Bootstrap encoder must return normalized vectors")
        from threadpoolctl import threadpool_limits
        full_vectors = np.zeros((len(records), 384), dtype=np.float64)
        eligible_rows = [i for i, r in enumerate(records) if (r["abstract"] or "").strip()]
        full_vectors[eligible_rows] = vectors
        with threadpool_limits(limits=1):
            formation = form_interest_model(full_vectors, [r["key"] for r in records], [r["title"] for r in records],
                [r["abstract"] for r in records], dois=[r["doi"] for r in records])
        groups = [g for g in formation["qualification"]["clusters"] if g["published"]]
        if not groups and compatible and previous.centers:
            raise ValueError("Rebuild has no formal centers; retain last-known-good generation")
        revision = uuid4().hex
        centers = make_centers([r["key"] for r in eligible], vectors,
            [dict(rows=g["probe_strong_rows"], stability=0.) for g in groups], revision, LatentParameters(),
            previous if compatible else None, hashes)
        centers = [c.model_copy(update={"centroid": g["trimmed_centroid"],
            "representative_items": [r["key"] for r in g["representatives"]]}) for c, g in zip(centers, groups)]
        target = self.root / "generations" / revision
        temporary = target.with_name("." + revision)
        temporary.mkdir(parents=True, mode=0o700)
        try:
            np.save(temporary / "embeddings.npy", vectors, allow_pickle=False)
            model = CenterRecallModel(interest_model_revision=revision, source_snapshot_sha256=digest(records), encoder=encoder,
                embedding_text_fingerprint=fingerprint, threshold=.55,
                centers=[FormalCentroid(interest_id=c.interest_id, centroid=c.centroid) for c in centers])
            save_center_model(temporary / "model.json", model)
            generation = Generation(model_revision=revision, workspace_repository_id=repository_id,
                library_identity_sha256=library_identity, library_revision=snapshot.revision, input_sha256=digest(records),
                input_records=[InputRecord(**r) for r in records], built_at=now.isoformat(), embedding_fingerprint=fingerprint,
                clustering_runtime=runtime, item_text_hashes=hashes, embedding_keys=[r["key"] for r in eligible],
                embeddings_sha256=sha256((temporary / "embeddings.npy").read_bytes()).hexdigest(),
                model_sha256=sha256((temporary / "model.json").read_bytes()).hexdigest(),
                previous_model_revision=previous.model_revision if compatible else None, centers=centers, formation=formation)
            for private_file in temporary.iterdir():
                private_file.chmod(0o600)
            raw = canonical(generation.model_dump())
            (temporary / "generation.json").write_bytes(raw)
            (temporary / "generation.json").chmod(0o600)
            deployment = Deployment(deployment_revision=uuid4().hex, previous_deployment_revision=prior.deployment_revision if prior else None,
                workspace_repository_id=repository_id, library_identity_sha256=library_identity, library_revision=snapshot.revision,
                input_sha256=generation.input_sha256, interest_model_revision=revision, model_sha256=generation.model_sha256,
                generation_sha256=sha256(raw).hexdigest(), encoder=encoder, embedding_text_fingerprint=fingerprint,
                activated_at=now.isoformat(), activation_reason=reason)
            current = storage.read_profile_snapshot()
            if (current.library_identity_sha256, current.revision, records_for(current)) != (library_identity, snapshot.revision, records):
                raise ValueError("Library changed during accepted build")
            lease.validate_for(self.state)
            temporary.replace(target)
            self.load(deployment, repository_id, library_identity)
            self._switch(deployment, expected, lease)
            return deployment, reason, len(changed)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def rollback(self, deployment_revision, repository_id, library_identity, expected, cache):
        if not __import__('re').fullmatch(REVISION, deployment_revision):
            raise ValueError("Invalid rollback revision")
        with StateCoordinator(self.state).acquire() as lease:
            prior = self.current()
            self.load(prior, repository_id, library_identity)
            target = Deployment.model_validate_json((self.root / "deployments" / (deployment_revision + ".json")).read_bytes())
            generation, _, _ = self.load(target, repository_id, library_identity)
            if not generation.centers:
                raise ValueError("Rollback requires last-known-good formal centers")
            validate_encoder(cache, target.encoder)
            active = Deployment.model_validate({**target.model_dump(), "deployment_revision": uuid4().hex,
                "previous_deployment_revision": prior.deployment_revision, "activated_at": datetime.now(timezone.utc).isoformat(),
                "activation_reason": "rollback"})
            self._switch(active, expected, lease)
            return active


def load_runtime(store, repository_id, library_identity, cache):
    from .recall_integration import LatentRecallRuntime, LatentRecallUnavailable
    try:
        deployment = store.current()
        _, _, model_path = store.load(deployment, repository_id, library_identity)
        runtime = LatentRecallRuntime(model_path, cache)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise LatentRecallUnavailable("LATENT_RECALL_ARTIFACT_UNAVAILABLE") from None
    try:
        validate_encoder(cache, deployment.encoder)
    except (OSError, ValueError):
        raise LatentRecallUnavailable("LATENT_RECALL_ENCODER_UNAVAILABLE") from None
    return deployment, runtime


class RunDeploymentEvidence(Closed):
    schema_name: Literal["zotwatch-latent-deployment-evidence"] = "zotwatch-latent-deployment-evidence"
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=REVISION)
    deployment: Deployment | None
    deployment_sha256: str | None = Field(pattern=SHA256)

    @model_validator(mode="after")
    def checksum(self):
        if self.deployment_sha256 != (digest(self.deployment.model_dump()) if self.deployment else None):
            raise ValueError("Run deployment checksum mismatch")
        return self

    def validate_result(self, result):
        metadata = result.evidence.latent_recall if result.evidence and result.schema_version == 3 else None
        if self.run_id != result.run_id:
            raise ValueError("Exact run deployment mismatch")
        if metadata and (not self.deployment or metadata.interest_model_revision != self.deployment.interest_model_revision or
            metadata.embedding_text_fingerprint != self.deployment.embedding_text_fingerprint):
            raise ValueError("Exact run latent revision mismatch")
        if result.status == "succeeded" and result.command == "watch" and (not metadata or not self.deployment):
            raise ValueError("Missing exact run deployment")
