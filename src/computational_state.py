from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .faiss_store import FaissIndex
from .models import ZoteroItem
from .utils import json_dumps


MANIFEST_SCHEMA_VERSION = 1
POINTER_SCHEMA_VERSION = 1
STATE_COMPATIBILITY_VERSION = 1
PROFILE_BUILDER_ABI = 1
PROFILE_SCHEMA_VERSION = 1
EMBEDDING_INPUT_SCHEMA = "zotero-item-embedding-v1"
EMBEDDING_NORMALIZATION = "l2-float32-v1"
PROFILE_AGGREGATION = "mean-l2-v1"
FAISS_FORMAT_VERSION = 1
FAISS_FACTORY = "IndexFlatIP"
FAISS_METRIC = "inner-product"


class ComputationalStateError(RuntimeError):
    """Base class for computational-state failures."""


class StateCompatibilityError(ComputationalStateError):
    """The current generation is complete but cannot be used by this run."""


class StateCorruptionError(ComputationalStateError):
    """The current pointer, manifest, or one of its artifacts is invalid."""


class StateFutureSchemaError(StateCompatibilityError):
    """The current state was produced by a newer manifest schema."""


class StateChangedDuringBuild(ComputationalStateError):
    """The committed SQLite mirror changed before publication."""


@dataclass
class StateLease:
    state_dir: Path
    _owner: "StateCoordinator"
    _active: bool = True

    def validate(self, coordinator: "StateCoordinator") -> None:
        if not self._active or self._owner is not coordinator:
            raise RuntimeError("State lease is inactive or belongs to another coordinator")

    def validate_for(self, state_dir: Path | str) -> None:
        if not self._active or self.state_dir != Path(state_dir).resolve():
            raise RuntimeError("State lease is inactive or belongs to another state directory")


class StateCoordinator:
    """Own the one non-reentrant cross-process lease for an official run."""

    def __init__(self, state_dir: Path | str):
        self.state_dir = Path(state_dir).resolve()
        self.lock_path = self.state_dir / ".zotwatch-state.lock"
        self._active_lease: StateLease | None = None

    @contextmanager
    def acquire(self) -> Iterator[StateLease]:
        if self._active_lease is not None:
            raise RuntimeError("StateCoordinator lease is deliberately non-reentrant")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        _lock_stream(stream)
        lease = StateLease(state_dir=self.state_dir, _owner=self)
        self._active_lease = lease
        try:
            yield lease
        finally:
            lease._active = False
            self._active_lease = None
            _unlock_stream(stream)
            stream.close()

    def validate(self, lease: StateLease) -> None:
        lease.validate(self)
        if lease.state_dir != self.state_dir:
            raise RuntimeError("State lease belongs to another state directory")


def _lock_stream(stream) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows runners later
        import msvcrt

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock_stream(stream) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows runners later
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_engine_version() -> str:
    try:
        return importlib_metadata.version("ZotWatch")
    except importlib_metadata.PackageNotFoundError:
        return "2.0.0.dev1"


def pseudonymous_library_identity(library_type: str, library_id: str) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "library_id": str(library_id),
                "library_type": library_type,
                "source": "zotero",
            }
        )
    )


@dataclass(frozen=True)
class EmbeddingRuntimeDescriptor:
    provider: str
    model_identifier: str
    model_revision: str | None
    artifact_identity: str
    dimension: int | None = None
    input_schema: str = EMBEDDING_INPUT_SCHEMA
    normalization: str = EMBEDDING_NORMALIZATION
    profile_builder_abi: int = PROFILE_BUILDER_ABI
    diagnostic_probe_fingerprint: str | None = None

    def compatibility_payload(self) -> dict[str, Any]:
        return {
            "artifact_identity": self.artifact_identity,
            "dimension": self.dimension,
            "input_schema": self.input_schema,
            "model_identifier": self.model_identifier,
            "model_revision": self.model_revision,
            "normalization": self.normalization,
            "profile_builder_abi": self.profile_builder_abi,
            "provider": self.provider,
        }

    @property
    def hard_fingerprint_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.compatibility_payload()))


def descriptor_for_vectorizer(vectorizer: Any) -> EmbeddingRuntimeDescriptor:
    factory = getattr(vectorizer, "runtime_descriptor", None)
    if callable(factory):
        descriptor = factory()
        if not isinstance(descriptor, EmbeddingRuntimeDescriptor):
            raise TypeError("vectorizer.runtime_descriptor() returned an invalid descriptor")
        return descriptor
    model_identifier = str(getattr(vectorizer, "model_name", type(vectorizer).__name__))
    artifact_identity = getattr(vectorizer, "artifact_identity", None)
    if not artifact_identity:
        artifact_identity = (
            f"legacy-vectorizer:{type(vectorizer).__module__}."
            f"{type(vectorizer).__qualname__}:{model_identifier}"
        )
    return EmbeddingRuntimeDescriptor(
        provider=str(getattr(vectorizer, "provider", "local")),
        model_identifier=model_identifier,
        model_revision=getattr(vectorizer, "model_revision", None),
        artifact_identity=str(artifact_identity),
        dimension=getattr(vectorizer, "dimension", None),
        diagnostic_probe_fingerprint=getattr(
            vectorizer, "diagnostic_probe_fingerprint", None
        ),
    )


def profile_config_fingerprint(
    descriptor: EmbeddingRuntimeDescriptor,
    *,
    semantic_overrides: Mapping[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "embedding": descriptor.compatibility_payload(),
        "faiss": {"factory": FAISS_FACTORY, "metric": FAISS_METRIC},
        "item_selection": "active-zotero-mirror-v1",
        "profile": {
            "aggregation": PROFILE_AGGREGATION,
            "schema_version": PROFILE_SCHEMA_VERSION,
            "top_authors": 20,
            "top_venues": 20,
        },
    }
    if semantic_overrides:
        payload["semantic_overrides"] = dict(semantic_overrides)
    return sha256_bytes(canonical_json_bytes(payload))


def embedding_input_payload(item: ZoteroItem) -> dict[str, Any]:
    return {
        "abstract": item.abstract,
        "creators": list(item.creators),
        "schema": EMBEDDING_INPUT_SCHEMA,
        "tags": list(item.tags),
        "title": item.title,
    }


def embedding_input_fingerprint(item: ZoteroItem) -> str:
    return sha256_bytes(canonical_json_bytes(embedding_input_payload(item)))


def library_snapshot_fingerprints(items: Iterable[ZoteroItem]) -> tuple[str, str]:
    snapshot_entries: list[dict[str, Any]] = []
    input_entries: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: value.key):
        input_hash = embedding_input_fingerprint(item)
        input_entries.append({"input_sha256": input_hash, "key": item.key})
        snapshot_entries.append(
            {
                "input_sha256": input_hash,
                "key": item.key,
                "venue": item.raw.get("data", {}).get("publicationTitle"),
                "version": item.version,
            }
        )
    return (
        sha256_bytes(canonical_json_bytes(snapshot_entries)),
        sha256_bytes(canonical_json_bytes(input_entries)),
    )


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GenerationMetadata(_StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    run_id: str = Field(min_length=1, max_length=128)
    created_at: str = Field(min_length=1, max_length=64)


class EngineMetadata(_StrictModel):
    version: str = Field(min_length=1, max_length=128)
    profile_builder_abi: int


class LibraryMetadata(_StrictModel):
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=0)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_count: int = Field(ge=1)


class EmbeddingMetadata(_StrictModel):
    provider: str
    model_identifier: str
    model_revision: str | None
    artifact_identity: str
    model_fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    diagnostic_probe_fingerprint: str | None = None
    input_schema: str
    normalization: str
    input_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimension: int = Field(gt=0)
    artifact: Literal["embeddings.npz"]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProfileMetadata(_StrictModel):
    schema_version: int
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    aggregation: str
    artifact: Literal["profile.json"]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class IndexMetadata(_StrictModel):
    format: Literal["faiss"]
    format_version: int
    factory: str
    metric: str
    artifact: Literal["faiss.index"]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimension: int = Field(gt=0)
    ntotal: int = Field(ge=1)


class StateManifest(_StrictModel):
    schema_version: Literal[1]
    state_compatibility_version: int
    generation: GenerationMetadata
    engine: EngineMetadata
    library: LibraryMetadata
    embedding: EmbeddingMetadata
    profile: ProfileMetadata
    index: IndexMetadata


class CurrentPointer(_StrictModel):
    schema_version: Literal[1]
    generation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StateExpectation:
    library_identity_sha256: str
    library_revision: int
    library_snapshot_sha256: str
    embedding_input_set_sha256: str
    item_count: int
    embedding: EmbeddingRuntimeDescriptor
    profile_config_sha256: str


def expectation_from_snapshot(
    snapshot: Any,
    descriptor: EmbeddingRuntimeDescriptor,
    *,
    profile_config_sha256: str | None = None,
) -> StateExpectation:
    return StateExpectation(
        library_identity_sha256=snapshot.library_identity_sha256,
        library_revision=snapshot.revision,
        library_snapshot_sha256=snapshot.snapshot_sha256,
        embedding_input_set_sha256=snapshot.embedding_input_set_sha256,
        item_count=len(snapshot.items),
        embedding=descriptor,
        profile_config_sha256=(
            profile_config_sha256 or profile_config_fingerprint(descriptor)
        ),
    )


@dataclass(frozen=True)
class StateHandle:
    generation_id: str
    generation_dir: Path
    manifest_path: Path
    profile_path: Path
    embeddings_path: Path
    index_path: Path
    manifest: StateManifest
    profile: dict[str, Any]


class StateManager:
    def __init__(
        self,
        state_dir: Path | str,
        *,
        coordinator: StateCoordinator | None = None,
    ):
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "computational"
        self.generations_dir = self.root / "generations"
        self.staging_dir = self.root / "staging"
        self.current_path = self.root / "current.json"
        self.coordinator = coordinator or StateCoordinator(self.state_dir)

    @contextmanager
    def lease_scope(self, lease: StateLease | None = None) -> Iterator[StateLease]:
        if lease is not None:
            self.coordinator.validate(lease)
            yield lease
            return
        with self.coordinator.acquire() as owned:
            yield owned

    def load_current(self, expectation: StateExpectation) -> StateHandle:
        pointer_data = self._read_json(self.current_path, "current pointer")
        schema_version = pointer_data.get("schema_version") if isinstance(pointer_data, dict) else None
        if isinstance(schema_version, int) and schema_version > POINTER_SCHEMA_VERSION:
            raise StateFutureSchemaError("Current pointer uses a future schema version")
        pointer = self._validate_model(CurrentPointer, pointer_data, "current pointer")

        generation_dir = self.generations_dir / pointer.generation_id
        manifest_path = generation_dir / "state-manifest.json"
        if not generation_dir.is_dir():
            raise StateCorruptionError("Current generation directory is missing")
        if not manifest_path.is_file() or sha256_file(manifest_path) != pointer.manifest_sha256:
            raise StateCorruptionError("Current manifest checksum does not match pointer")

        manifest_data = self._read_json(manifest_path, "state manifest")
        manifest_schema = (
            manifest_data.get("schema_version") if isinstance(manifest_data, dict) else None
        )
        if isinstance(manifest_schema, int) and manifest_schema > MANIFEST_SCHEMA_VERSION:
            raise StateFutureSchemaError("State manifest uses a future schema version")
        manifest = self._validate_model(StateManifest, manifest_data, "state manifest")
        self._check_compatibility(manifest, expectation)

        profile_path = generation_dir / manifest.profile.artifact
        embeddings_path = generation_dir / manifest.embedding.artifact
        index_path = generation_dir / manifest.index.artifact
        self._check_file(profile_path, manifest.profile.artifact_sha256, "profile")
        self._check_file(embeddings_path, manifest.embedding.artifact_sha256, "embeddings")
        self._check_file(index_path, manifest.index.artifact_sha256, "FAISS index")

        profile = self._read_json(profile_path, "profile")
        self._validate_artifacts(
            manifest,
            profile=profile,
            embeddings_path=embeddings_path,
            index_path=index_path,
        )
        return StateHandle(
            generation_id=pointer.generation_id,
            generation_dir=generation_dir,
            manifest_path=manifest_path,
            profile_path=profile_path,
            embeddings_path=embeddings_path,
            index_path=index_path,
            manifest=manifest,
            profile=profile,
        )

    def publish_generation(
        self,
        *,
        expectation: StateExpectation,
        vectors: np.ndarray,
        keys: list[str],
        profile: dict[str, Any],
        verify_current: Callable[[], StateExpectation],
        lease: StateLease | None = None,
        created_at: str | None = None,
    ) -> StateHandle:
        with self.lease_scope(lease) as active_lease:
            self.coordinator.validate(active_lease)
            return self._publish_generation_locked(
                expectation=expectation,
                vectors=vectors,
                keys=keys,
                profile=profile,
                verify_current=verify_current,
                created_at=created_at,
            )

    def _publish_generation_locked(
        self,
        *,
        expectation: StateExpectation,
        vectors: np.ndarray,
        keys: list[str],
        profile: dict[str, Any],
        verify_current: Callable[[], StateExpectation],
        created_at: str | None,
    ) -> StateHandle:
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != expectation.item_count:
            raise ValueError("Embedding matrix does not match the library snapshot")
        if vectors.shape[0] == 0 or vectors.shape[1] == 0:
            raise ValueError("A computational generation requires non-empty embeddings")
        if len(keys) != expectation.item_count or len(set(keys)) != len(keys):
            raise ValueError("Embedding keys do not match the library snapshot")
        if expectation.embedding.dimension not in (None, vectors.shape[1]):
            raise StateCompatibilityError("Runtime embedding dimension changed during build")

        run_id = uuid.uuid4().hex
        generation_id = (
            f"{expectation.library_revision}-"
            f"{expectation.library_snapshot_sha256[:12]}-{run_id[:12]}"
        )
        timestamp = created_at or datetime.now(timezone.utc).isoformat()
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.generations_dir.mkdir(parents=True, exist_ok=True)
        staging = self.staging_dir / f"{run_id}.tmp"
        final_dir = self.generations_dir / generation_id
        staging.mkdir()
        profile_path = staging / "profile.json"
        embeddings_path = staging / "embeddings.npz"
        index_path = staging / "faiss.index"
        manifest_path = staging / "state-manifest.json"
        renamed = False
        try:
            _write_text_fsync(profile_path, json_dumps(profile, indent=2))
            with embeddings_path.open("wb") as stream:
                np.savez(stream, vectors=vectors, keys=np.asarray(keys, dtype=np.str_))
                stream.flush()
                os.fsync(stream.fileno())
            index, _ = FaissIndex.from_vectors(vectors)
            index.save(index_path)
            _fsync_file(index_path)

            descriptor = expectation.embedding
            manifest = StateManifest(
                schema_version=MANIFEST_SCHEMA_VERSION,
                state_compatibility_version=STATE_COMPATIBILITY_VERSION,
                generation=GenerationMetadata(
                    id=generation_id,
                    run_id=run_id,
                    created_at=timestamp,
                ),
                engine=EngineMetadata(
                    version=installed_engine_version(),
                    profile_builder_abi=PROFILE_BUILDER_ABI,
                ),
                library=LibraryMetadata(
                    identity_sha256=expectation.library_identity_sha256,
                    revision=expectation.library_revision,
                    snapshot_sha256=expectation.library_snapshot_sha256,
                    item_count=expectation.item_count,
                ),
                embedding=EmbeddingMetadata(
                    provider=descriptor.provider,
                    model_identifier=descriptor.model_identifier,
                    model_revision=descriptor.model_revision,
                    artifact_identity=descriptor.artifact_identity,
                    model_fingerprint_sha256=descriptor.hard_fingerprint_sha256,
                    diagnostic_probe_fingerprint=descriptor.diagnostic_probe_fingerprint,
                    input_schema=descriptor.input_schema,
                    normalization=descriptor.normalization,
                    input_set_sha256=expectation.embedding_input_set_sha256,
                    dimension=vectors.shape[1],
                    artifact="embeddings.npz",
                    artifact_sha256=sha256_file(embeddings_path),
                ),
                profile=ProfileMetadata(
                    schema_version=PROFILE_SCHEMA_VERSION,
                    config_sha256=expectation.profile_config_sha256,
                    aggregation=PROFILE_AGGREGATION,
                    artifact="profile.json",
                    artifact_sha256=sha256_file(profile_path),
                ),
                index=IndexMetadata(
                    format="faiss",
                    format_version=FAISS_FORMAT_VERSION,
                    factory=FAISS_FACTORY,
                    metric=FAISS_METRIC,
                    artifact="faiss.index",
                    artifact_sha256=sha256_file(index_path),
                    dimension=vectors.shape[1],
                    ntotal=vectors.shape[0],
                ),
            )
            _write_text_fsync(
                manifest_path,
                json_dumps(manifest.model_dump(mode="json"), indent=2),
            )
            self._validate_staged(staging, manifest, profile)

            if verify_current() != expectation:
                raise StateChangedDuringBuild(
                    "Committed Zotero library changed during profile generation"
                )

            os.replace(staging, final_dir)
            renamed = True
            _fsync_directory(self.generations_dir)
            final_manifest = final_dir / "state-manifest.json"
            pointer = CurrentPointer(
                schema_version=POINTER_SCHEMA_VERSION,
                generation_id=generation_id,
                manifest_sha256=sha256_file(final_manifest),
            )
            self.root.mkdir(parents=True, exist_ok=True)
            pointer_tmp = self.root / f"current.{run_id}.tmp"
            _write_text_fsync(
                pointer_tmp,
                json_dumps(pointer.model_dump(mode="json"), indent=2),
            )
            os.replace(pointer_tmp, self.current_path)
            _fsync_directory(self.root)
            return self.load_current(expectation)
        finally:
            if not renamed and staging.exists():
                shutil.rmtree(staging)

    def _validate_staged(
        self,
        staging: Path,
        manifest: StateManifest,
        profile: dict[str, Any],
    ) -> None:
        self._check_file(
            staging / manifest.profile.artifact,
            manifest.profile.artifact_sha256,
            "profile",
        )
        self._check_file(
            staging / manifest.embedding.artifact,
            manifest.embedding.artifact_sha256,
            "embeddings",
        )
        self._check_file(
            staging / manifest.index.artifact,
            manifest.index.artifact_sha256,
            "FAISS index",
        )
        self._validate_artifacts(
            manifest,
            profile=profile,
            embeddings_path=staging / manifest.embedding.artifact,
            index_path=staging / manifest.index.artifact,
        )

    @staticmethod
    def _read_json(path: Path, label: str) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StateCorruptionError(f"Could not read {label}: {path}") from exc

    @staticmethod
    def _validate_model(model_type, data: Any, label: str):
        try:
            return model_type.model_validate(data)
        except ValidationError as exc:
            raise StateCorruptionError(f"Invalid {label}") from exc

    @staticmethod
    def _check_file(path: Path, expected_sha256: str, label: str) -> None:
        if not path.is_file():
            raise StateCorruptionError(f"Current {label} artifact is missing")
        if sha256_file(path) != expected_sha256:
            raise StateCorruptionError(f"Current {label} checksum does not match manifest")

    @staticmethod
    def _check_compatibility(
        manifest: StateManifest,
        expectation: StateExpectation,
    ) -> None:
        checks = (
            (
                manifest.state_compatibility_version == STATE_COMPATIBILITY_VERSION,
                "state compatibility version",
            ),
            (
                manifest.engine.profile_builder_abi == PROFILE_BUILDER_ABI,
                "profile builder ABI",
            ),
            (
                manifest.library.identity_sha256 == expectation.library_identity_sha256,
                "library identity",
            ),
            (manifest.library.revision == expectation.library_revision, "library revision"),
            (
                manifest.library.snapshot_sha256 == expectation.library_snapshot_sha256,
                "library snapshot",
            ),
            (manifest.library.item_count == expectation.item_count, "library item count"),
            (
                manifest.embedding.model_fingerprint_sha256
                == expectation.embedding.hard_fingerprint_sha256,
                "embedding model",
            ),
            (
                manifest.embedding.input_schema == expectation.embedding.input_schema,
                "embedding input schema",
            ),
            (
                manifest.embedding.input_set_sha256
                == expectation.embedding_input_set_sha256,
                "embedding input set",
            ),
            (
                manifest.embedding.normalization == expectation.embedding.normalization,
                "embedding normalization",
            ),
            (
                expectation.embedding.dimension is None
                or manifest.embedding.dimension == expectation.embedding.dimension,
                "embedding dimension",
            ),
            (
                manifest.profile.config_sha256 == expectation.profile_config_sha256,
                "profile configuration",
            ),
            (manifest.profile.schema_version == PROFILE_SCHEMA_VERSION, "profile schema"),
            (manifest.profile.aggregation == PROFILE_AGGREGATION, "profile aggregation"),
            (manifest.index.format_version == FAISS_FORMAT_VERSION, "FAISS format"),
            (manifest.index.factory == FAISS_FACTORY, "FAISS factory"),
            (manifest.index.metric == FAISS_METRIC, "FAISS metric"),
        )
        for compatible, label in checks:
            if not compatible:
                raise StateCompatibilityError(f"Current state has incompatible {label}")

    @staticmethod
    def _validate_artifacts(
        manifest: StateManifest,
        *,
        profile: Any,
        embeddings_path: Path,
        index_path: Path,
    ) -> None:
        if not isinstance(profile, dict):
            raise StateCorruptionError("Profile artifact must contain a JSON object")
        centroid = profile.get("centroid")
        if not isinstance(centroid, list) or len(centroid) != manifest.embedding.dimension:
            raise StateCorruptionError("Profile centroid dimension does not match manifest")
        if profile.get("item_count") != manifest.library.item_count:
            raise StateCorruptionError("Profile item count does not match manifest")
        if profile.get("model") != manifest.embedding.model_identifier:
            raise StateCorruptionError("Profile model does not match manifest")

        try:
            with np.load(embeddings_path, allow_pickle=False) as payload:
                vectors = payload["vectors"]
                keys = payload["keys"]
        except Exception as exc:
            raise StateCorruptionError("Could not load embeddings artifact") from exc
        expected_shape = (manifest.library.item_count, manifest.embedding.dimension)
        if vectors.dtype != np.float32 or vectors.shape != expected_shape:
            raise StateCorruptionError("Embedding matrix does not match manifest")
        if keys.ndim != 1 or len(keys) != manifest.library.item_count:
            raise StateCorruptionError("Embedding keys do not match manifest")
        if len(set(str(key) for key in keys.tolist())) != len(keys):
            raise StateCorruptionError("Embedding keys contain duplicates")

        try:
            index = FaissIndex.load(index_path)
        except Exception as exc:
            raise StateCorruptionError("Could not load FAISS index") from exc
        if index.dim != manifest.index.dimension or index.index.ntotal != manifest.index.ntotal:
            raise StateCorruptionError("FAISS metadata does not match manifest")
        if manifest.index.dimension != manifest.embedding.dimension:
            raise StateCorruptionError("FAISS and embedding dimensions differ")
        if manifest.index.ntotal != manifest.library.item_count:
            raise StateCorruptionError("FAISS and library item counts differ")
        if type(index.index).__name__ != manifest.index.factory:
            raise StateCorruptionError("FAISS factory does not match manifest")


def _write_text_fsync(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":  # pragma: no cover - directory fsync is POSIX-specific
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "ComputationalStateError",
    "CurrentPointer",
    "EmbeddingRuntimeDescriptor",
    "StateChangedDuringBuild",
    "StateCompatibilityError",
    "StateCorruptionError",
    "StateExpectation",
    "StateFutureSchemaError",
    "StateHandle",
    "StateManager",
    "StateManifest",
    "StateCoordinator",
    "StateLease",
    "canonical_json_bytes",
    "descriptor_for_vectorizer",
    "embedding_input_fingerprint",
    "expectation_from_snapshot",
    "installed_engine_version",
    "library_snapshot_fingerprints",
    "profile_config_fingerprint",
    "pseudonymous_library_identity",
    "sha256_bytes",
    "sha256_file",
]
