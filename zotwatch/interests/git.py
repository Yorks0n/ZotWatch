"""Read only a committed, fixed-path interest snapshot. No checkpoint fallback."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

import requests

from .contract import BRANCH, PROFILE_PATH, MAX_BYTES, SHA_PATTERN, InterestError, Profile, blob_sha, parse_profile


@dataclass(frozen=True)
class Snapshot:
    feedback_commit_sha: str
    profile_blob_sha: str
    profile: Profile


def snapshot(commit: str, sha: str, content: bytes) -> Snapshot:
    if not re.fullmatch(SHA_PATTERN, commit) or not re.fullmatch(SHA_PATTERN, sha):
        raise InterestError("INTEREST_GIT_INVALID")
    if blob_sha(content) != sha:
        raise InterestError("INTEREST_GIT_INVALID")
    return Snapshot(commit, sha, parse_profile(content))


def read_local(workspace: Path) -> Snapshot | None:
    def git(*args):
        return subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, timeout=15)
    if git("rev-parse", "--git-dir").returncode:
        return None  # Ordinary legacy CLI workspaces need not be Git repositories.
    ref = git("show-ref", "--verify", "--hash", f"refs/heads/{BRANCH}")
    if ref.returncode == 1:
        return None
    if ref.returncode:
        raise InterestError("INTEREST_UNAVAILABLE")
    commit = ref.stdout.decode().strip()
    tree = git("ls-tree", commit, "--", PROFILE_PATH)
    if tree.returncode:
        raise InterestError("INTEREST_UNAVAILABLE")
    if not tree.stdout:
        return None
    entry = tree.stdout.decode().split()
    if len(entry) != 4 or entry[:2] != ["100644", "blob"] or entry[3] != PROFILE_PATH:
        raise InterestError("INTEREST_GIT_INVALID")
    sha = entry[2]
    size = git("cat-file", "-s", sha)
    if size.returncode or int(size.stdout) > MAX_BYTES:
        raise InterestError("INTEREST_TOO_LARGE")
    body = git("cat-file", "blob", sha)
    if body.returncode:
        raise InterestError("INTEREST_UNAVAILABLE")
    return snapshot(commit, sha, body.stdout)


def read_github(repository: str, repository_id: int, token: str, *, session=None) -> Snapshot | None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
        raise InterestError("INTEREST_UNAVAILABLE")
    client = session or requests.Session()
    root = "https://api.github.com/repos/" + repository

    def get(suffix, missing=False):
        try:
            with client.get(root + suffix, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }, timeout=15, allow_redirects=False, stream=True) as response:
                if missing and response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise InterestError("INTEREST_UNAVAILABLE")
                chunks, size = [], 0
                for chunk in response.iter_content(8192):
                    size += len(chunk)
                    if size > 131072:
                        raise InterestError("INTEREST_TOO_LARGE")
                    chunks.append(chunk)
                import json
                return json.loads(b"".join(chunks))
        except (requests.RequestException, ValueError, TypeError):
            raise InterestError("INTEREST_UNAVAILABLE") from None

    repo = get("")
    if repo.get("id") != repository_id or repo.get("private") is not True or repo.get("archived") or repo.get("disabled"):
        raise InterestError("INTEREST_REPOSITORY_MISMATCH")
    ref = get("/git/ref/heads/" + BRANCH, missing=True)
    if ref is None:
        return None
    obj = ref.get("object", {})
    commit = obj.get("sha", "")
    if obj.get("type") != "commit" or not re.fullmatch(SHA_PATTERN, commit):
        raise InterestError("INTEREST_GIT_INVALID")
    data = get(f"/git/commits/{commit}")
    if data.get("sha") != commit:
        raise InterestError("INTEREST_GIT_INVALID")
    current = data.get("tree", {}).get("sha", "")
    parts = PROFILE_PATH.split("/")
    for index, part in enumerate(parts):
        if not re.fullmatch(SHA_PATTERN, current):
            raise InterestError("INTEREST_GIT_INVALID")
        tree = get(f"/git/trees/{current}")
        if tree.get("sha") != current or tree.get("truncated") or not isinstance(tree.get("tree"), list):
            raise InterestError("INTEREST_GIT_INVALID")
        entries = [e for e in tree["tree"] if e.get("path") == part]
        if not entries:
            return None
        if len(entries) != 1:
            raise InterestError("INTEREST_GIT_INVALID")
        entry = entries[0]
        mode, kind = ("100644", "blob") if index == len(parts)-1 else ("040000", "tree")
        if entry.get("mode") != mode or entry.get("type") != kind:
            raise InterestError("INTEREST_GIT_INVALID")
        current = entry.get("sha", "")
    if not re.fullmatch(SHA_PATTERN, current):
        raise InterestError("INTEREST_GIT_INVALID")
    content = get(f"/git/blobs/{current}")
    if content.get("sha") != current or content.get("encoding") != "base64" or type(content.get("size")) is not int or not 0 <= content["size"] <= MAX_BYTES:
        raise InterestError("INTEREST_GIT_INVALID")
    try:
        body = base64.b64decode("".join(content["content"].split()), validate=True)
        if len(body) != content["size"]:
            raise ValueError()
        return snapshot(commit, content["sha"], body)
    except (KeyError, ValueError, TypeError):
        raise InterestError("INTEREST_GIT_INVALID") from None


def readiness(value: Snapshot | None, repository_id: int, library_id: str) -> str:
    if value is None:
        return "not_ready"
    if value.profile.workspace_repository_id != repository_id:
        raise InterestError("INTEREST_REPOSITORY_MISMATCH")
    if value.profile.library_scope.id != library_id:
        raise InterestError("INTEREST_LIBRARY_MISMATCH")
    return "ready" if any(t.status == "active" for t in value.profile.interests) else "paused"
