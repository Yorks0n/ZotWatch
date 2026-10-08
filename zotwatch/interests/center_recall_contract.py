"""Small closed centroid-only runtime artifact; no historical member payload."""
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

RECALL_POLICY = "center-recall-v1"
V1_THRESHOLD = 0.55
MAX_CENTERS = 60
MAX_MODEL_BYTES = 2 * 1024 * 1024
SHA256 = r"^[0-9a-f]{64}$"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return sha256(canonical(value)).hexdigest()


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class EncoderIdentity(Closed):
    model_identifier: Literal["sentence-transformers/all-MiniLM-L6-v2"]
    model_revision: Literal["1110a243fdf4706b3f48f1d95db1a4f5529b4d41"]
    dimension: Literal[384]
    text_policy: Literal["title-abstract-token-windows-128-roundtrip-safe-mean-v2"]
    artifact_sha256: dict[str, str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def valid_artifacts(self):
        import re
        for name, checksum in self.artifact_sha256.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or not name or len(name) > 200 or not re.fullmatch(SHA256, checksum):
                raise ValueError("Invalid encoder artifact identity")
        if "model.safetensors" not in self.artifact_sha256:
            raise ValueError("Missing fixed encoder weight identity")
        return self


class FormalCentroid(Closed):
    interest_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    centroid: list[float] = Field(min_length=384, max_length=384)

    @model_validator(mode="after")
    def unit_centroid(self):
        if not np.isclose(np.linalg.norm(self.centroid), 1., atol=1e-6, rtol=0):
            raise ValueError("Formal centroid must be normalized")
        return self


class CenterRecallModel(Closed):
    schema_name: Literal["zotwatch-center-recall-model"] = "zotwatch-center-recall-model"
    schema_version: Literal[1] = 1
    interest_model_revision: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_snapshot_sha256: str = Field(pattern=SHA256)
    encoder: EncoderIdentity
    embedding_text_fingerprint: str = Field(pattern=SHA256)
    recall_policy: Literal["center-recall-v1"] = RECALL_POLICY
    threshold: float = Field(ge=0., le=1.)
    centers: list[FormalCentroid] = Field(max_length=MAX_CENTERS)

    @model_validator(mode="after")
    def valid_model(self):
        if self.embedding_text_fingerprint != digest(self.encoder.model_dump()):
            raise ValueError("Embedding/text-policy fingerprint mismatch")
        if len({center.interest_id for center in self.centers}) != len(self.centers):
            raise ValueError("Duplicate stable center ID")
        return self


def load_center_model(path):
    """Reads exactly one bounded JSON file; never opens the P5B3 capsule/vectors."""
    with Path(path).open("rb") as source:
        data = source.read(MAX_MODEL_BYTES + 1)
    if len(data) > MAX_MODEL_BYTES:
        raise ValueError("Center recall model exceeds byte budget")
    envelope = json.loads(data)
    if set(envelope) != {"model", "sha256"} or digest(envelope["model"]) != envelope["sha256"]:
        raise ValueError("Center recall model checksum mismatch")
    return CenterRecallModel.model_validate(envelope["model"])


def save_center_model(path, model):
    if not isinstance(model, CenterRecallModel):
        raise ValueError("Validated centroid model required")
    data = model.model_dump()
    payload = canonical({"model": data, "sha256": digest(data)})
    if len(payload) > MAX_MODEL_BYTES:
        raise ValueError("Center recall model exceeds byte budget")
    with Path(path).open("xb") as output:
        output.write(payload)
