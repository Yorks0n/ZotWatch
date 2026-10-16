from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

BRANCH = "zotwatch-feedback"
PROFILE_PATH = "feedback/interests/v1/profile.json"
MAX_BYTES = 65536
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
SHA_PATTERN = r"^[0-9a-f]{40}$"
UUID = Annotated[str, StringConstraints(pattern=UUID_PATTERN)]
SHA = Annotated[str, StringConstraints(pattern=SHA_PATTERN)]


class InterestError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class Topic(ClosedModel):
    id: UUID
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")
    description: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    priority: Literal["low", "normal", "high"]
    status: Literal["active", "muted"]
    horizon: Literal["long_term", "short_term"]


class LibraryScope(ClosedModel):
    type: Literal["user"]
    id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")


class Confirmation(ClosedModel):
    state: Literal["confirmed"]
    request_id: UUID
    confirmed_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


Preference = Annotated[str, StringConstraints(min_length=1, max_length=160, pattern=r"\S")]


class Preferences(ClosedModel):
    authors: list[Preference] = Field(max_length=20)
    venues: list[Preference] = Field(max_length=20)


class Profile(ClosedModel):
    schema_version: Literal[1]
    ranking_policy: Literal["topic-v1"]
    workspace_repository_id: int = Field(gt=0, le=9007199254740991)
    library_scope: LibraryScope
    confirmation: Confirmation
    interests: list[Topic] = Field(max_length=20)
    preferences: Preferences

    @model_validator(mode="after")
    def unique_ids(self):
        if len({topic.id for topic in self.interests}) != len(self.interests):
            raise ValueError("Duplicate topic identity")
        return self


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InterestError("INTEREST_INVALID")
        result[key] = value
    return result


def parse_profile(content: bytes) -> Profile:
    if len(content) > MAX_BYTES:
        raise InterestError("INTEREST_TOO_LARGE")
    try:
        data = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
        # JSON Schema's integer vs boolean distinction must hold in both runtimes.
        if type(data.get("schema_version")) is not int:
            raise ValueError()
        profile = Profile.model_validate(data)
        from datetime import datetime
        datetime.strptime(profile.confirmation.confirmed_at, "%Y-%m-%dT%H:%M:%SZ")
        return profile
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise InterestError("INTEREST_INVALID") from None


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
