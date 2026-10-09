"""Independent private Git metadata cache; reuse existing ownership/API/CAS."""
import argparse
import base64
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
from src.computational_state import StateCoordinator
from zotwatch.interests.center_recall_contract import canonical
from zotwatch.interests.lifecycle_transport import GitState
from .metadata import Cache, NAME, MAX_BYTES

BRANCH = "zotwatch-metadata-cache-v1"
MARKER = "metadata-remote-v1.json"


class MetadataGitState(GitState):
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
                raise ValueError("Metadata branch tree invalid")
            entry = tree["tree"][0]
            if entry["path"] != NAME or entry["type"] != "blob" or entry["mode"] != "100644" or entry["size"] > MAX_BYTES:
                raise ValueError("Metadata branch entry invalid")
            blob = self.api("GET", f"/git/blobs/{entry['sha']}")
            if blob["encoding"] != "base64" or blob["size"] > MAX_BYTES:
                raise ValueError("Metadata blob invalid")
            data = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
            if len(data) != entry["size"] or sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != entry["sha"]:
                raise ValueError("Metadata Git blob digest mismatch")
            Cache.validate(data, self.repository_id)
            with StateCoordinator(state).acquire():
                temporary = state / (NAME + ".tmp")
                temporary.write_bytes(data)
                temporary.replace(state / NAME)
        (state / MARKER).write_bytes(canonical({"repository_id":self.repository_id,"head":head}))
        return head

    def publish(self, state):
        state = Path(state)
        marker = json.loads((state / MARKER).read_bytes())
        if set(marker) != {"repository_id","head"} or marker["repository_id"] != self.repository_id:
            raise ValueError("Metadata restore authority required")
        expected = marker["head"]
        with StateCoordinator(state).acquire():
            data = (state / NAME).read_bytes()
            Cache.validate(data, self.repository_id)
        if self.head() != expected:
            raise ValueError("Remote metadata CAS conflict")
        blob = self.api("POST", "/git/blobs", {"content":base64.b64encode(data).decode(),"encoding":"base64"})
        tree = self.api("POST", "/git/trees", {"tree":[{"path":NAME,"mode":"100644","type":"blob","sha":blob["sha"]}]})
        if expected:
            current = self.api("GET", f"/git/commits/{expected}")
            if current["tree"]["sha"] == tree["sha"]:
                return {"commit_sha":expected,"cache_sha256":sha256(data).hexdigest(),"unchanged":True}
        commit = self.api("POST", "/git/commits", {"message":"Private DOI metadata cache v1","tree":tree["sha"],"parents":[expected] if expected else []})
        if expected:
            self.api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha":commit["sha"],"force":False})
        else:
            self.api("POST", "/git/refs", {"ref":f"refs/heads/{BRANCH}","sha":commit["sha"]})
        (state / MARKER).write_bytes(canonical({"repository_id":self.repository_id,"head":commit["sha"]}))
        return {"commit_sha":commit["sha"],"cache_sha256":sha256(data).hexdigest(),"unchanged":False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["restore","publish"])
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    remote = MetadataGitState(os.environ["GITHUB_REPOSITORY"],int(os.environ["GITHUB_REPOSITORY_ID"]),os.environ["GITHUB_TOKEN"])
    print(json.dumps({"restored_commit_sha":remote.restore(args.state)} if args.command == "restore" else remote.publish(args.state)))


if __name__ == "__main__":
    main()
