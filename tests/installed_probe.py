"""Standalone subprocess probe copied outside the repo; never shipped in the wheel."""
import json
import os
from pathlib import Path
import socket
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

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
    model_revision = "synthetic-e0-revision-1"
    artifact_identity = "synthetic-e0-vector-table-v1"
    dimension = 3

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
pointer = json.loads((state / "computational/current.json").read_text())
generation = state / "computational/generations" / pointer["generation_id"]

# E5A: an installed Basic v2 workspace uses the same E3/E4/ranking/writer pipeline.
v2_workspace = workspace.parent / (workspace.name + "-v2")
v2_workspace.mkdir()
(v2_workspace / "zotwatch.yaml").write_text("""schema_version: 2
zotero:
  library_type: user
candidates:
  provider: public-api-v1
  sources: [crossref, arxiv, biorxiv]
  window_days: 7
ranking:
  policy: legacy-v1
  top_n: 3
  max_preprint_ratio: 0.3
embedding:
  provider: local
  model: sentence-transformers/all-MiniLM-L6-v2
ai:
  services: {}
  features:
    rerank: {enabled: false}
    summary: {enabled: false}
outputs:
  formats: [rss, html]
  publish: false
""")
ingest_zotero_api.ZoteroIngestor.run = lambda *a, **kw: SimpleNamespace(
    fetched=0, updated=0, removed=0
)
v2_reports = workspace.parent / (workspace.name + "-v2-reports")
v2_common = [
    "--workspace", str(v2_workspace),
    "--state-dir", str(state),
    "--reports-dir", str(v2_reports),
]
assert public_main(["profile", *v2_common]) == 0
assert public_main(["watch", *v2_common]) == 0
assert (v2_reports / "feed.xml").read_bytes() == (reports / "feed.xml").read_bytes()
assert (v2_reports / "report-20260114.html").is_file()
print(json.dumps({
    "cli_module": cli.__file__,
    "state": str(state),
    "reports": str(reports),
    "profile": str(generation / "profile.json"),
    "manifest": str(generation / "state-manifest.json"),
    "v2_feed": str(v2_reports / "feed.xml"),
    "v2_report": str(v2_reports / "report-20260114.html"),
}))
