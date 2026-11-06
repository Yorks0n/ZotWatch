#!/usr/bin/env python3
"""Build the public page package from this job's local results. Never commit it."""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import github_pages_rss as rss


def prepare(result_path, delivery_path, destination, feed_url, previous=None):
    final = json.loads(Path(result_path).read_bytes())
    if (final.get("schema_name") != "zotwatch-latent-auto-run-result"
            or final.get("status") != "succeeded" or final.get("command") != "watch"
            or final.get("exit_code") != 0 or not isinstance(final.get("recommendations"), list)):
        raise ValueError("No successful recommendation result")
    if delivery_path.is_file():
        final = rss.apply_delivery_sidecar(final, json.loads(delivery_path.read_bytes()), verify_source=False)
    old = previous.read_bytes() if previous and previous.is_file() else None
    # The deployed RSS is authoritative; a failed read must not erase its history.
    try:
        old = rss.request(feed_url, limit=8 * 1024 * 1024)
        rss.read_feed(old)
    except HTTPError as exc:
        if exc.code != 404:
            raise
    data, count = rss.merge_feed(final, old, feed_url)
    if data is None:
        root = rss.ET.Element("rss", {"version": "2.0"})
        channel = rss.ET.SubElement(root, "channel")
        for key, value in (("title", "ZotWatch RSS"), ("link", feed_url),
                           ("description", "Public metadata of recommended papers.")):
            rss.ET.SubElement(channel, key).text = value
        data = rss.ET.tostring(root, encoding="utf-8", xml_declaration=True)
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in rss.delivery_assets(data, feed_url).items():
        (destination / name).write_bytes(content)
    if previous:
        previous.parent.mkdir(parents=True, exist_ok=True)
        previous.write_bytes(data)
    return {"items": len(rss.read_feed(data)), "new_items": count}


def notify(config, artifact_id):
    endpoint = config["handoff_url"]
    url = urlsplit(endpoint)
    if url.scheme != "https" or url.path != "/api/publication/handoff" or url.query or url.fragment:
        raise ValueError("Invalid publication endpoint")
    payload = {"repository_id": int(os.environ["GITHUB_REPOSITORY_ID"]),
               "run_id": int(os.environ["GITHUB_RUN_ID"]), "artifact_id": int(artifact_id)}
    req = Request(endpoint, method="POST", data=json.dumps(payload).encode(), headers={
        "Authorization": "Bearer " + os.environ["ZOTWATCH_PUBLISH_TOKEN"],
        "Content-Type": "application/json", "User-Agent": "ZotWatch-Pages/1.0"})
    with urlopen(req, timeout=45) as response:
        if response.status != 202:
            raise ValueError("Publication was not accepted")
    return {"publication": "accepted"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "notify"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--delivery", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--artifact-id")
    args = parser.parse_args()
    config = json.loads(args.config.read_bytes())
    target = config["target_repository"]
    if not rss.REPOSITORY.fullmatch(target):
        raise ValueError("Invalid publication repository")
    owner, name = target.split("/")
    expected = f"https://{owner.lower()}.github.io/" + ("" if name.lower() == f"{owner.lower()}.github.io" else name + "/") + "feed.xml"
    if config["feed_url"] != expected:
        raise ValueError("Invalid RSS address")
    value = notify(config, args.artifact_id) if args.operation == "notify" else prepare(
        args.result, args.delivery, args.destination, expected, args.previous)
    print(json.dumps(value))


if __name__ == "__main__":
    main()
