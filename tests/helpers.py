import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
GOLDENS = Path(__file__).parent / "goldens"
NOW = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return NOW.replace(tzinfo=None)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class FixedVectors:
    model_name = "synthetic-e0-3d-v1"
    model_revision = "synthetic-e0-revision-1"
    artifact_identity = "synthetic-e0-vector-table-v1"

    def encode(self, texts):
        vectors = read_json(FIXTURES / "vectors.json")
        # Only the remote embedding boundary is replaced. Unexpected input fails.
        return np.asarray([vectors[text.split("\n")[0]] for text in texts], dtype="float32")


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self.payload


def assert_json(actual, expected):
    """Preserve every field/order; tolerate only floating point roundoff."""
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_json(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            assert_json(left, right)
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=1e-6, rel=0)
    else:
        assert actual == expected


def ids(works):
    return [work.identifier for work in works]
