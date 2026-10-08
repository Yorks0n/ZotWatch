"""Operator preparation only: restore the exact public snapshot, verify every byte.

The recommendation runtime stays offline and never invokes this script.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument("--model", required=True)
parser.add_argument("--cache", required=True)
args = parser.parse_args()
identity = json.loads(Path(args.model).read_bytes())["model"]["encoder"]
assert identity["model_identifier"] == "sentence-transformers/all-MiniLM-L6-v2"
assert identity["model_revision"] == "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
snapshot = Path(args.cache) / "models--sentence-transformers--all-MiniLM-L6-v2/snapshots" / identity["model_revision"]
for name, checksum in sorted(identity["artifact_sha256"].items()):
    target = snapshot / name
    if target.is_file() and sha256(target.read_bytes()).hexdigest() == checksum:
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".download")
    digest = sha256()
    size = 0
    url = f"https://huggingface.co/{identity['model_identifier']}/resolve/{identity['model_revision']}/{name}"
    with urlopen(url, timeout=120) as response, temporary.open("wb") as stream:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 128 * 1024 * 1024:
                raise ValueError("Snapshot file exceeds budget")
            digest.update(chunk)
            stream.write(chunk)
    if digest.hexdigest() != checksum:
        raise ValueError("Exact frozen encoder checksum mismatch")
    temporary.replace(target)
print(json.dumps({"encoder_revision": identity["model_revision"], "verified_files": len(identity["artifact_sha256"])}))
