"""Standalone subprocess probe copied outside the repo; never shipped in the wheel."""
import json
import os
from pathlib import Path
import socket
import sys
from datetime import datetime, timezone

import numpy as np
import requests


def no_network(*args, **kwargs):
    raise AssertionError("Installed probe forbids network")


socket.socket.connect = no_network
socket.create_connection = no_network
socket.getaddrinfo = no_network
requests.sessions.Session.send = no_network

# Importing the engine must not load any .env.
assert os.getenv("E1_DOTENV") is None
from src import build_profile, cli, fetch_new, ingest_zotero_api, report_html, rss_writer, score_rank
from src.models import CandidateWork
from src.settings import load_settings
from zotwatch.cli import main as public_main
from zotwatch.resources import journal_metrics_path
assert os.getenv("E1_DOTENV") is None

workspace = Path.cwd()
fixtures = workspace / "fixtures"
now = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return now.replace(tzinfo=None)


class Vectors:
    model_name = "synthetic-e0-3d-v1"

    def encode(self, texts):
        values = json.loads((fixtures / "vectors.json").read_text())
        return np.asarray([values[t.split("\n")[0]] for t in texts], dtype="float32")


class Response:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}

    def json(self):
        return self.data


for module in (cli, fetch_new, report_html, rss_writer, score_rank):
    module.datetime = Clock
build_profile.utc_now = fetch_new.utc_now = lambda: now
build_profile.TextVectorizer = score_rank.TextVectorizer = Vectors
rows = json.loads((fixtures / "zotero.json").read_text())
responses = iter([Response(rows, headers={"Last-Modified-Version": "10"}),
                  Response([], status=304), Response({"items": []})])
ingest_zotero_api.request_with_retry = lambda *a, **kw: next(responses)
candidates = [CandidateWork(**r) for r in json.loads((fixtures / "candidates.json").read_text())]
fetch_new.CandidateFetcher._fetch_public_candidates = lambda *a: candidates
fetch_new.CandidateFetcher._fetch_crossref_top_venues = lambda *a: []
entry = public_main if sys.argv[1] == "new" else cli.main
# All mutable outputs are siblings of the workspace, not engine package files.
state = workspace.parent / (workspace.name + "-state")
reports = workspace.parent / (workspace.name + "-reports")
common = ["--state-dir", str(state), "--reports-dir", str(reports)]
entry(["profile", "--full", *common])  # Default workspace must be cwd.
assert os.environ["E1_DOTENV"] == "workspace"
assert os.getenv("E1_PARENT_DOTENV") is None
metrics = fixtures / "journal_metrics.csv"
entry(["watch", "--rss", "--report", "--top", "3", "--journal-metrics", str(metrics), *common])
settings = load_settings(workspace)
ranker = score_rank.WorkRanker(workspace, settings, Vectors(), state_dir=state, metrics_path=metrics)
ranked = ranker.rank(candidates)
(reports / "ranking.json").write_text(json.dumps([w.model_dump(mode="json") for w in ranked]))
rss_writer.write_rss(ranked, reports / "full.xml")
report_html.render_html(ranked, reports / "full.html")
with journal_metrics_path("bundled", workspace) as bundled:
    assert bundled.is_file()
    assert score_rank.WorkRanker(workspace, settings, Vectors(), state_dir=state,
                                metrics_path=bundled).journal_metrics
print(json.dumps({"cli_module": cli.__file__, "state": str(state), "reports": str(reports)}))
