"""P5C2 private deployment transport; no latent state in the engine repository."""
from __future__ import annotations

import argparse
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

from .center_recall_contract import load_center_model
from .recall_integration import LatentRecallUnavailable

MODEL_REVISION = "316ccd15ed658f0c3cf89142b4ba026a"
MODEL_SHA256 = "0b9bca4b84b25f94d1762d61f3ec4e22b9af4eff6d7c2b68b795cfe2af25321a"
EMBEDDING_FINGERPRINT = "340252ec18dd3f961b7f234c30857c51c851445705628f2e4200695020e5daa0"
BUNDLE_SHA256 = "d82a23ab99b0916610c398d2a5de98006efcc826000ce1331db0dedf908293fe"
RELEASE_TAG = f"zotwatch-center-recall-v1-{MODEL_REVISION}"
ASSET_NAME = "latent-runtime-v1.tar.gz"
SNAPSHOT = Path("hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41")
MAX_BUNDLE_BYTES = 128 * 1024 * 1024


def file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model(path):
    try:
        model = load_center_model(path)
        if (file_hash(path) != MODEL_SHA256 or model.interest_model_revision != MODEL_REVISION or
                model.embedding_text_fingerprint != EMBEDDING_FINGERPRINT or model.threshold != .55 or not model.centers):
            raise ValueError()
        return model
    except (OSError, ValueError, TypeError, KeyError):
        raise LatentRecallUnavailable("LATENT_RECALL_ARTIFACT_UNAVAILABLE") from None


def validate_runtime(model_path, cache):
    model = validate_model(model_path)
    try:
        snapshot = Path(cache) / SNAPSHOT.relative_to("hub")
        for name, checksum in model.encoder.artifact_sha256.items():
            if file_hash(snapshot / name) != checksum:
                raise ValueError()
    except (OSError, ValueError):
        raise LatentRecallUnavailable("LATENT_RECALL_ENCODER_UNAVAILABLE") from None
    return model


def pack(model_path, cache, output):
    model = validate_runtime(model_path, cache)
    files = {Path("model.json"): Path(model_path)}
    files.update({SNAPSHOT / name: Path(cache) / SNAPSHOT.relative_to("hub") / name
                  for name in model.encoder.artifact_sha256})
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for name, source in sorted(files.items()):
                info = tarfile.TarInfo(name.as_posix())
                info.size, info.mode, info.mtime = source.stat().st_size, 0o600, 0
                with source.open("rb") as stream:
                    archive.addfile(info, stream)
    if output.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("Runtime bundle exceeds deployment budget")
    return {"bundle_sha256": file_hash(output), "model_sha256": MODEL_SHA256,
            "interest_model_revision": MODEL_REVISION, "embedding_text_fingerprint": EMBEDDING_FINGERPRINT}


def unpack(bundle, destination):
    destination = Path(destination)
    if destination.exists() or Path(bundle).stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("Invalid runtime destination or size")
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) > 33 or len(names) != len(set(names)) or any(
            not member.isfile() or PurePosixPath(member.name).is_absolute() or ".." in PurePosixPath(member.name).parts or
            "\\" in member.name or member.size > MAX_BUNDLE_BYTES for member in members
        ) or sum(member.size for member in members) > MAX_BUNDLE_BYTES:
            raise ValueError("Invalid runtime archive")
        model_member = archive.getmember("model.json")
        if model_member.size > 2097152:
            raise ValueError("Invalid model size")
        with tempfile.TemporaryDirectory() as temp:
            model_path = Path(temp) / "model.json"
            model_path.write_bytes(archive.extractfile(model_member).read())
            model = validate_model(model_path)
        allowed = {"model.json", *((SNAPSHOT / name).as_posix() for name in model.encoder.artifact_sha256)}
        if set(names) != allowed:
            raise ValueError("Runtime file set mismatch")
        archive.extractall(destination, filter="data")
    # The runner checks encoder files after profile readiness, so an unavailable
    # cache becomes a persisted v3 not_ready result with its ordinary receipt.


def fetch(repository, token, destination):
    import requests
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
        raise ValueError("Private caller identity unavailable")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2026-03-10"}
    base = f"https://api.github.com/repos/{repository}"
    response = requests.get(base, headers=headers, timeout=30)
    response.raise_for_status()
    if response.json().get("private") is not True:
        raise ValueError("Runtime repository must be private")
    release = requests.get(f"{base}/releases/tags/{RELEASE_TAG}", headers=headers, timeout=30)
    release.raise_for_status()
    assets = [asset for asset in release.json().get("assets", []) if asset.get("name") == ASSET_NAME]
    if len(assets) != 1 or not isinstance(assets[0].get("id"), int) or not 0 < assets[0].get("size", 0) <= MAX_BUNDLE_BYTES:
        raise ValueError("Runtime asset unavailable")
    asset = assets[0]
    digest = asset.get("digest", "")
    if digest != f"sha256:{BUNDLE_SHA256}":
        raise ValueError("Runtime asset digest unavailable")
    with tempfile.TemporaryDirectory() as temp:
        bundle = Path(temp) / ASSET_NAME
        # requests strips Authorization across the GitHub asset host redirect.
        with requests.get(f"{base}/releases/assets/{asset['id']}", headers={**headers, "Accept": "application/octet-stream"},
                          stream=True, timeout=(30, 120)) as download:
            download.raise_for_status()
            size = 0
            with bundle.open("xb") as stream:
                for chunk in download.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BUNDLE_BYTES:
                        raise ValueError("Runtime asset exceeds budget")
                    stream.write(chunk)
        if size != asset["size"] or f"sha256:{file_hash(bundle)}" != digest:
            raise ValueError("Runtime asset checksum mismatch")
        unpack(bundle, destination)
    return {"asset_id": asset["id"], "bundle_sha256": digest.removeprefix("sha256:"),
            "interest_model_revision": MODEL_REVISION, "model_sha256": MODEL_SHA256,
            "embedding_text_fingerprint": EMBEDDING_FINGERPRINT}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("pack")
    for name in ("model", "cache", "output"):
        build.add_argument("--" + name, required=True)
    download = sub.add_parser("fetch")
    download.add_argument("--destination", required=True)
    args = parser.parse_args()
    if args.command == "pack":
        print(json.dumps(pack(args.model, args.cache, args.output)))
    else:
        try:
            metadata = fetch(os.getenv("GITHUB_REPOSITORY", ""), os.getenv("GITHUB_TOKEN", ""), args.destination)
            print(json.dumps(metadata))
        except Exception:
            # Still run the ordinary runner to persist authoritative not_ready.
            print(json.dumps({"state": "not_ready", "reason": "LATENT_RECALL_ARTIFACT_UNAVAILABLE"}))


if __name__ == "__main__":
    main()
