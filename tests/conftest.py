import os
import socket
import time

import dotenv
import pytest
import requests
import yaml

from .helpers import FIXTURES, ROOT, FrozenDateTime, NOW, read_json


def forbidden_network(*args, **kwargs):
    raise AssertionError("E0 forbids network; supply an explicit synthetic response")


# Install before importing production modules: cli imports load_dotenv eagerly.
_guard = pytest.MonkeyPatch()
_guard.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
_guard.setattr(socket.socket, "connect", forbidden_network)
_guard.setattr(socket, "create_connection", forbidden_network)
_guard.setattr(socket, "getaddrinfo", forbidden_network)
_guard.setattr(requests.sessions.Session, "send", forbidden_network)
_guard.setattr(time, "sleep", lambda *a: None)
for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
    _guard.setenv(key, "1")

from src import build_profile, cli, fetch_new, report_html, rss_writer, score_rank
from src.models import CandidateWork, ZoteroItem
from src.settings import load_settings
from src.storage import ProfileStorage


def pytest_unconfigure(config):
    _guard.undo()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if any(word in key for word in ("ZOTERO", "SUPABASE", "MAILTO", "ALTMETRIC")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("ZOTERO_API_KEY", "synthetic-zotero-key")
    monkeypatch.setenv("ZOTERO_USER_ID", "123456")
    for module in (cli, fetch_new, report_html, rss_writer, score_rank):
        monkeypatch.setattr(module, "datetime", FrozenDateTime)
    monkeypatch.setattr(build_profile, "utc_now", lambda: NOW)
    monkeypatch.setattr(fetch_new, "utc_now", lambda: NOW)


@pytest.fixture
def workspace(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    for name in ("zotero", "sources", "scoring"):
        data = yaml.safe_load((ROOT / "config" / f"{name}.yaml").read_text())
        if name == "sources":
            data["public_api"]["base_url"] = "https://public.example.invalid/functions/v1"
            data["public_api"]["publishable_key"] = "synthetic-public-key"
        (config / f"{name}.yaml").write_text(yaml.safe_dump(data))
    return tmp_path


@pytest.fixture
def settings(workspace):
    return load_settings(workspace)


@pytest.fixture
def storage(workspace):
    db = ProfileStorage(workspace / "data" / "profile.sqlite")
    db.initialize()
    yield db
    db.close()


@pytest.fixture
def library(storage):
    for row in read_json(FIXTURES / "zotero.json"):
        storage.upsert_item(ZoteroItem.from_zotero_api(row), content_hash="synthetic-initial-hash")
    storage.set_library_identity_sha256("a" * 64)
    storage.set_last_modified_version(10)
    return storage


@pytest.fixture
def candidates():
    return [CandidateWork(**row) for row in read_json(FIXTURES / "candidates.json")]
