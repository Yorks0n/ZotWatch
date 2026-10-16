"""Private, rebuildable latent state; independent of confirmed semantic interests."""
from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

REVISION = r"^[0-9a-f]{32}$"
SHA256 = r"^[0-9a-f]{64}$"


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class LatentParameters(Closed):
    min_cluster_size: int = Field(default=10, ge=3)
    min_samples: int = Field(default=5, ge=1)
    selection: Literal["leaf", "eom"] = "leaf"
    merge_similarity: float = Field(default=0.92, ge=0.0, le=1.0)
    min_membership: float = Field(default=0.2, ge=0.0, le=1.0)
    representative_count: int = Field(default=5, ge=1, le=10)


class LatentCenter(Closed):
    interest_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    centroid: list[float] = Field(min_length=1)
    member_keys: list[str] = Field(min_length=3)
    member_count: int = Field(ge=3)
    cohesion: float = Field(ge=0.0, le=1.0)
    stability: float = Field(ge=0.0, le=1.0)
    representative_items: list[str] = Field(min_length=1, max_length=10)
    model_revision: str = Field(pattern=REVISION)
    previous_interest_ids: list[str] = Field(default_factory=list)
    change: Literal["new", "stable", "changed", "split", "merged", "reorganized"]
    semantic_refresh_required: bool

    @model_validator(mode="after")
    def valid_center(self):
        if self.member_count != len(set(self.member_keys)) or self.member_keys != sorted(set(self.member_keys)):
            raise ValueError("Invalid center membership")
        if len(set(self.representative_items)) != len(self.representative_items) or not set(self.representative_items) <= set(self.member_keys):
            raise ValueError("Invalid representatives")
        if not np.isclose(np.linalg.norm(self.centroid), 1.0, atol=1e-6):
            raise ValueError("Centroid must be a unit vector")
        return self


class LatentModel(Closed):
    schema_name: Literal["zotwatch-latent-interest-model"] = "zotwatch-latent-interest-model"
    schema_version: Literal[1] = 1
    model_revision: str = Field(pattern=REVISION)
    built_at: str
    library_identity_sha256: str = Field(pattern=SHA256)
    library_revision: int = Field(ge=0)
    embedding_fingerprint: str = Field(pattern=SHA256)
    text_policy: Literal["title-abstract-token-windows-128-mean-v1"] = "title-abstract-token-windows-128-mean-v1"
    clustering_runtime: str
    parameters: LatentParameters
    item_text_hashes: dict[str, str]
    embedding_keys: list[str]
    embeddings_sha256: str = Field(pattern=SHA256)
    noise_keys: list[str]
    excluded_keys: list[str]
    centers: list[LatentCenter]

    @model_validator(mode="after")
    def valid_partition(self):
        from datetime import datetime
        if datetime.fromisoformat(self.built_at).utcoffset() is None:
            raise ValueError("Model time requires a timezone")
        groups = [self.noise_keys, self.excluded_keys, *[c.member_keys for c in self.centers]]
        flat = [key for group in groups for key in group]
        if len(flat) != len(set(flat)) or set(flat) != set(self.item_text_hashes):
            raise ValueError("Model must partition the complete library")
        if any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in self.item_text_hashes.values()):
            raise ValueError("Invalid text fingerprint")
        if self.embedding_keys != sorted(set(self.item_text_hashes) - set(self.excluded_keys)):
            raise ValueError("Invalid embedding key order")
        if len({c.interest_id for c in self.centers}) != len(self.centers):
            raise ValueError("Duplicate interest IDs")
        if any(c.model_revision != self.model_revision for c in self.centers):
            raise ValueError("Center revision mismatch")
        if len({len(c.centroid) for c in self.centers}) > 1:
            raise ValueError("Inconsistent center dimensions")
        return self
