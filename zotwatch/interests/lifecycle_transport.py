"""Versioned private latent-state transport, independent of checkpoint-v1.

Durable Git branch commits carry the state archive. Ref fast-forward is the
remote CAS: concurrent children of the restored parent cannot both publish.
Encoder weights are deliberately absent from user state.
"""
from __future__ import annotations

import argparse
import base64
import gzip
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

from src.computational_state import StateCoordinator
from .center_recall_contract import canonical, digest
from .lifecycle import Deployment, LifecycleStore, accepted_encoder, validate_encoder

BRANCH = "zotwatch-latent-state-v1"
MAX_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
ARCHIVE_NAME = "latent-state-v1.tar.gz"


def checked_files(store, repository_id):
    current = store.current()
    if current is None or current.workspace_repository_id != repository_id:
        raise ValueError("Private latent current unavailable")
    files = {"current.json": store.root / "current.json"}
    deployments = sorted((store.root / "deployments").glob("*.json"))
    if not deployments or len(deployments) > 1024:
        raise ValueError("Latent deployment retention budget exceeded")
    seen = {}
    for path in deployments:
        d = Deployment.model_validate_json(path.read_bytes())
        if path.stem != d.deployment_revision:
            raise ValueError("Deployment filename mismatch")
        store.load(d, repository_id, d.library_identity_sha256)
        seen[d.deployment_revision] = d
        files[path.relative_to(store.root).as_posix()] = path
        for name in ("model.json", "generation.json", "embeddings.npy"):
            relative = f"generations/{d.interest_model_revision}/{name}"
            files[relative] = store.root / relative
    if current.deployment_revision not in seen or seen[current.deployment_revision] != current:
        raise ValueError("Current deployment immutable copy mismatch")
    for d in seen.values():
        if d.previous_deployment_revision and d.previous_deployment_revision not in seen:
            raise ValueError("Deployment lineage missing")
    if any(p.is_symlink() or not p.is_file() for p in files.values()):
        raise ValueError("Unsafe latent state file")
    return files


def pack(store, repository_id):
    files = checked_files(store, repository_id)
    manifest = canonical({"schema_name": "zotwatch-latent-state", "schema_version": 1,
        "workspace_repository_id": repository_id,
        "files": {name: sha256(path.read_bytes()).hexdigest() for name, path in files.items()}})
    payloads = {name: path.read_bytes() for name, path in files.items()}
    payloads["manifest.json"] = manifest
    if sum(map(len, payloads.values())) > MAX_BYTES:
        raise ValueError("Latent state exceeds retention budget")
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as zipfile:
        with tarfile.open(fileobj=zipfile, mode="w") as archive:
            for name, data in sorted(payloads.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(data), 0o600, 0
                archive.addfile(info, io.BytesIO(data))
    data = out.getvalue()
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Latent archive exceeds budget")
    return data


def unpack(data, state, repository_id):
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Latent archive exceeds budget")
    store = LifecycleStore(state)
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
        expanded = compressed.read(MAX_BYTES + 4 * 1024 * 1024 + 1)
    if len(expanded) > MAX_BYTES + 4 * 1024 * 1024:
        raise ValueError("Latent archive expansion exceeds budget")
    with StateCoordinator(state).acquire() as lease, tempfile.TemporaryDirectory(dir=Path(state)) as temp:
        staged = LifecycleStore(temp)
        staged.root.mkdir()
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            members = archive.getmembers()
            names = [m.name for m in members]
            pattern = r"(?:current\.json|manifest\.json|deployments/[0-9a-f]{32}\.json|generations/[0-9a-f]{32}/(?:model\.json|generation\.json|embeddings\.npy))"
            if len(names) > 4098 or len(names) != len(set(names)) or any(not m.isfile() or not re.fullmatch(pattern, m.name) or m.size > MAX_BYTES for m in members) or sum(m.size for m in members) > MAX_BYTES:
                raise ValueError("Invalid latent archive members")
            manifest = json.loads(archive.extractfile("manifest.json").read())
            if set(manifest) != {"schema_name", "schema_version", "workspace_repository_id", "files"} or manifest["schema_name"] != "zotwatch-latent-state" or manifest["schema_version"] != 1 or manifest["workspace_repository_id"] != repository_id or set(manifest["files"]) != set(names) - {"manifest.json"}:
                raise ValueError("Latent archive manifest mismatch")
            for member in members:
                if member.name == "manifest.json":
                    continue
                raw = archive.extractfile(member).read()
                if sha256(raw).hexdigest() != manifest["files"][member.name]:
                    raise ValueError("Latent archive checksum mismatch")
                path = staged.root / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        if set(checked_files(staged, repository_id)) != set(manifest["files"]):
            raise ValueError("Latent archive closed file set mismatch")
        lease.validate_for(store.state)
        if store.root.exists():
            raise ValueError("Restore requires a fresh latent state destination")
        staged.root.replace(store.root)
    return store.current()


class GitState:
    def __init__(self, repository, repository_id, token, session=None):
        import requests
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
            raise ValueError("Private repository credentials required")
        self.session = session or requests.Session()
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
        self.base = f"https://api.github.com/repos/{repository}"
        self.repository_id = repository_id
        repo = self.api("GET", "")
        if repo.get("id") != repository_id or repo.get("private") is not True or repo.get("archived") or repo.get("disabled"):
            raise ValueError("Numeric private repository ownership mismatch")

    def api(self, method, path, body=None, missing=False):
        response = self.session.request(method, self.base + path, headers=self.headers, json=body, timeout=(30, 120))
        if missing and response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def head(self):
        value = self.api("GET", f"/git/ref/heads/{BRANCH}", missing=True)
        return value["object"]["sha"] if value else None

    def restore(self, state):
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True)
        head = self.head()
        if head:
            commit = self.api("GET", f"/git/commits/{head}")
            tree = self.api("GET", f"/git/trees/{commit['tree']['sha']}")
            if tree.get("truncated") is not False or len(tree["tree"]) != 1:
                raise ValueError("Latent branch tree invalid")
            entry = tree["tree"][0]
            if entry["path"] != ARCHIVE_NAME or entry["type"] != "blob" or entry["mode"] != "100644" or entry["size"] > MAX_ARCHIVE:
                raise ValueError("Latent branch archive invalid")
            blob = self.api("GET", f"/git/blobs/{entry['sha']}")
            if blob["encoding"] != "base64" or blob["size"] > MAX_ARCHIVE:
                raise ValueError("Latent state blob invalid")
            data = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
            if len(data) != entry["size"]:
                raise ValueError("Latent state blob size mismatch")
            # Git object identity independently pins exact blob bytes.
            from hashlib import sha1
            if sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != entry["sha"]:
                raise ValueError("Latent Git blob digest mismatch")
            unpack(data, state, self.repository_id)
        (state / "latent-remote-v1.json").write_bytes(canonical({"repository_id": self.repository_id, "head": head}))
        return head

    def publish(self, state):
        state = Path(state)
        marker = json.loads((state / "latent-remote-v1.json").read_bytes())
        if set(marker) != {"repository_id", "head"} or marker["repository_id"] != self.repository_id:
            raise ValueError("Restore authority required before publish")
        expected = marker["head"]
        with StateCoordinator(state).acquire():
            data = pack(LifecycleStore(state), self.repository_id)
        if self.head() != expected:
            raise ValueError("Remote latent CAS conflict")
        blob = self.api("POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        tree = self.api("POST", "/git/trees", {"tree": [{"path": ARCHIVE_NAME, "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
        commit = self.api("POST", "/git/commits", {"message": "Private latent-state-v1 generation", "tree": tree["sha"], "parents": [expected] if expected else []})
        if expected:
            self.api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": commit["sha"], "force": False})
        else:
            self.api("POST", "/git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
        (state / "latent-remote-v1.json").write_bytes(canonical({"repository_id": self.repository_id, "head": commit["sha"]}))
        return {"commit_sha": commit["sha"], "archive_sha256": sha256(data).hexdigest(), "repository_id": self.repository_id}


def prepare_encoder(cache):
    """Deliver only exact generic weights; never fetch a personal frozen model."""
    encoder = accepted_encoder()
    from huggingface_hub import snapshot_download
    snapshot_download(encoder.model_identifier, revision=encoder.model_revision, cache_dir=cache,
        allow_patterns=list(encoder.artifact_sha256))
    validate_encoder(cache)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["restore", "publish", "encoder", "rollback"])
    parser.add_argument("--state")
    parser.add_argument("--cache")
    parser.add_argument("--library-identity")
    parser.add_argument("--deployment-revision")
    parser.add_argument("--expected-current")
    args = parser.parse_args()
    if args.command == "encoder":
        prepare_encoder(args.cache)
        print(json.dumps({"encoder_fingerprint": digest(accepted_encoder().model_dump())}))
        return
    repository_id = int(os.environ["GITHUB_REPOSITORY_ID"])
    remote = GitState(os.environ["GITHUB_REPOSITORY"], repository_id, os.environ["GITHUB_TOKEN"])
    if args.command == "restore":
        print(json.dumps({"restored_commit_sha": remote.restore(args.state)}))
    else:
        if args.command == "rollback":
            LifecycleStore(args.state).rollback(args.deployment_revision, repository_id,
                args.library_identity, args.expected_current, args.cache)
        print(json.dumps(remote.publish(args.state)))


if __name__ == "__main__":
    main()
