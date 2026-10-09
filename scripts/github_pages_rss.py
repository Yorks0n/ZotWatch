#!/usr/bin/env python3
"""RSS-only delivery of finalized recommendations; Python standard library only.

Run in the private workspace's separate workflow. Never copy/extract an artifact
into the Pages checkout. Only feed.xml and a constant index.html may be pushed.
"""
import argparse
import hashlib
from html import escape
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile


INDEX = ('<!doctype html>\n<html lang="en"><meta charset="utf-8">'
         '<title>ZotWatch RSS</title><link rel="alternate" '
         'type="application/rss+xml" title="ZotWatch RSS" href="feed.xml">'
         '<p><a href="feed.xml">Subscribe to ZotWatch RSS</a></p></html>\n')
ARTIFACT = "zotwatch-private-per-user-latent-run-v1"
SOURCE_PATHS = {".github/workflows/p5c4-lifecycle.yml"}
ITEM_FIELDS = {"title", "link", "guid", "pubDate", "description"}
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
DOI = re.compile(r"10\.\d{4,9}/[^\s?#]+", re.I)
ARXIV = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)
DESCRIPTION_PREFIX = '<div class="zotwatch-rss-description-v4">'


def request(url, token=None, limit=8 * 1024 * 1024):
    headers = {"User-Agent": "ZotWatch-RSS/1.0", "Accept": "application/json"}
    if token:
        if not url.startswith("https://api.github.com/"):
            raise ValueError("Credential destination rejected")
        headers["Authorization"] = f"Bearer {token}"
    # urllib drops Authorization when redirecting to another host below.
    if token:
        from urllib.request import HTTPRedirectHandler, build_opener

        class Redirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                redirected = super().redirect_request(req, fp, code, msg, hdrs, newurl)
                if redirected and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
                    redirected.remove_header("Authorization")
                return redirected

        response = build_opener(Redirect()).open(Request(url, headers=headers), timeout=45)
    else:
        response = urlopen(Request(url, headers=headers), timeout=45)
    with response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Response too large")
    return data


def github(path):
    return json.loads(request("https://api.github.com/" + path, os.environ["GITHUB_TOKEN"]))


def load_final(source, run_id):
    """Read the exact successful run, then just the finalized result in its ZIP."""
    run = github(f"repos/{source}/actions/runs/{run_id}")
    if run["repository"]["full_name"].lower() != source.lower():
        raise ValueError("Wrong source repository")
    if run["path"] not in SOURCE_PATHS:
        raise ValueError("Unrecognized recommendation workflow")
    if run["status"] != "completed" or run["conclusion"] != "success":
        return None
    artifacts = github(f"repos/{source}/actions/runs/{run_id}/artifacts?per_page=100")
    matching = [a for a in artifacts["artifacts"] if a["name"] == ARTIFACT and not a["expired"]]
    if len(matching) != 1:
        raise ValueError("Missing or ambiguous finalized artifact")
    artifact = matching[0]
    data = request(f"https://api.github.com/repos/{source}/actions/artifacts/{artifact['id']}/zip",
                   os.environ["GITHUB_TOKEN"])
    if artifact.get("digest") != "sha256:" + hashlib.sha256(data).hexdigest():
        raise ValueError("Artifact checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        result_names = [n for n in names if n in {"final/topic-result-v2.json", "final/topic-result-v3.json", "final/latent-auto-result-v1.json"}]
        if len(result_names) != 1 or len(names) != len(set(names)):
            raise ValueError("Ambiguous final result")
        def read_json(name):
            if archive.getinfo(name).file_size > 2 * 1024 * 1024:
                raise ValueError("Final result too large")
            return json.loads(archive.read(name))
        final = read_json(result_names[0])
        version = final.get("schema_version")
        if (final.get("schema_name"), version) not in {
                ("zotwatch-topic-run-result", 2), ("zotwatch-latent-topic-run-result", 3),
                ("zotwatch-latent-auto-run-result", 1)}:
            raise ValueError("Unrecognized final result")
        auto = final.get("schema_name") == "zotwatch-latent-auto-run-result"
        envelope = read_json("latent-auto-workflow-envelope-v1.json" if auto else f"topic-workflow-envelope-v{version}.json")
        if auto and final.get("status") == "succeeded" and final.get("command") == "watch":
            if (final.get("ranking_policy") != "latent-auto-v1" or final.get("candidate_policy") != "center-recall-v1"
                    or envelope.get("schema_name") != "zotwatch-latent-auto-workflow-envelope"
                    or envelope.get("schema_version") != 1 or envelope.get("evidence") != final.get("evidence")
                    or final["evidence"].get("workspace_repository_id") != run["repository"]["id"]):
                raise ValueError("Invalid latent-auto contract")
            sidecar = read_json("final/latent-deployment-evidence-v1.json")
            deployment = sidecar.get("deployment")
            checksum = hashlib.sha256(json.dumps(deployment, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            if (not deployment or sidecar.get("run_id") != final["run_id"] or sidecar.get("deployment_sha256") != checksum
                    or any(final["evidence"].get(k) != deployment.get(k) for k in
                        ("workspace_repository_id", "library_identity_sha256", "interest_model_revision", "embedding_text_fingerprint"))
                    or len(final["recommendations"]) > 20
                    or len({r["work_key"] for r in final["recommendations"]}) != len(final["recommendations"])
                    or any(r["score"] != r["center_cosine"] or not .55 <= r["score"] <= 1 or
                        r["primary_center_id"] not in final["evidence"]["formal_center_ids"] for r in final["recommendations"])):
                raise ValueError("Invalid latent-auto provenance")
        optional = None
        if auto and "rss-ai-v1.json" in names:
            try:
                optional = read_json("rss-ai-v1.json")
            except (ValueError, KeyError, TypeError):
                pass  # Invalid optional delivery data cannot suppress original recommendations.
    if (str(envelope["caller_run_id"]) != str(run_id)
            or str(envelope["workspace_repository_id"]) != str(run["repository"]["id"])
            or envelope["run_id"] != final["run_id"]
            or envelope["result_status"] != final["status"]):
        raise ValueError("Final result/run mismatch")
    if final["status"] != "succeeded" or final["command"] != "watch":
        return None
    if final["exit_code"] != 0 or not isinstance(final["recommendations"], list):
        raise ValueError("Invalid successful result")
    return apply_delivery_sidecar(final, optional) if auto and optional is not None else final


def apply_delivery_sidecar(final, value):
    """Validate a closed public projection bound to this exact immutable result."""
    try:
        if (set(value) != {"schema_name", "schema_version", "run_id", "source_final_sha256", "retained_work_keys", "papers"}
            or value["schema_name"] != "zotwatch-staging-rss-ai" or value["schema_version"] != 1
            or value["run_id"] != final["run_id"]
            or value["source_final_sha256"] != hashlib.sha256(json.dumps(final, ensure_ascii=False,
                sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()):
            raise ValueError("Wrong delivery source")
        keys = value["retained_work_keys"]
        original = final["recommendations"]
        selected = [r for r in original if r["work_key"] in keys]
        if (not isinstance(keys, list) or any(not isinstance(k, str) for k in keys)
            or keys != [r["work_key"] for r in selected] or not isinstance(value["papers"], list)
            or len(value["papers"]) != len(keys)):
            raise ValueError("Reordered, replaced or duplicate recommendations")
        for row, paper in zip(selected, value["papers"]):
            if (set(paper) != {"work_key", "title_en", "abstract_en", "title_zh", "abstract_zh"}
                or paper["work_key"] != row["work_key"] or plain(paper["title_en"]) != plain(row["title"])
                or not isinstance(paper["abstract_en"], str) or len(paper["abstract_en"]) > 6000
                or any(v is not None and (not isinstance(v, str) or len(v) > limit)
                       for v, limit in ((paper["title_zh"], 4000), (paper["abstract_zh"], 50000)))):
                raise ValueError("Nonpublic or invalid delivery data")
        return {**final, "recommendations": selected, "_staging_ai": {r["work_key"]: r for r in value["papers"]}}
    except (ValueError, TypeError, KeyError, AttributeError):
        return final


def identity(row):
    key = row.get("doi") or row.get("work_key", "")
    if DOI.fullmatch(key):
        doi = key.lower()
        return "urn:doi:" + doi, "https://doi.org/" + quote(doi, safe="/():;._-")
    if key.startswith("arxiv:") and ARXIV.fullmatch(key[6:]):
        identifier = re.sub(r"v\d+$", "", key[6:])
        return "urn:arxiv:" + identifier, "https://arxiv.org/abs/" + identifier
    raise ValueError("Recommendation lacks supported stable public identity")


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_endtag(self, tag):
        self.parts.append(" ")


def plain(value):
    if not isinstance(value, str):
        raise ValueError("Invalid public text")
    parser = PlainText()
    parser.feed(value)
    text = " ".join("".join(parser.parts).split())
    if len(text) > 100000 or any(ord(c) < 32 for c in text):
        raise ValueError("Invalid public text")
    return text


class AbstractHTML(HTMLParser):
    """Keep semantic JATS/HTML formatting without provider attributes or URLs."""
    TAGS = {"p": "p", "title": "h3", "h1": "h3", "h2": "h3", "h3": "h3",
            "h4": "h3", "h5": "h3", "h6": "h3", "sec": "div", "div": "div",
            "bold": "strong", "b": "strong", "strong": "strong",
            "italic": "em", "i": "em", "em": "em", "sup": "sup", "sub": "sub",
            "ul": "ul", "ol": "ol", "li": "li", "list": "ul", "list-item": "li"}
    BLOCKS = {"p", "h3", "div", "ul", "ol", "li"}

    def __init__(self, markup=False):
        super().__init__(convert_charrefs=True)
        self.parts, self.stack = [], []
        self.hidden = 0
        self.markup = markup

    def handle_starttag(self, tag, attrs):
        tag = tag.rsplit(":", 1)[-1]
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden:
            if tag in {"br", "break"}:
                self.parts.append("<br>")
            elif tag in self.TAGS:
                mapped = self.TAGS[tag]
                # JATS permits a list inside p; HTML does not. Close the HTML
                # paragraph first, and ignore its later unmatched source endtag.
                if mapped in self.BLOCKS and any(opened == "p" for _, opened in self.stack):
                    while self.stack:
                        _, opened = self.stack.pop()
                        self.parts.append("</" + opened + ">")
                        if opened == "p":
                            break
                self.parts.append("<" + mapped + ">")
                self.stack.append((tag, mapped))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.rsplit(":", 1)[-1] not in {"br", "break"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.rsplit(":", 1)[-1]
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and any(original == tag for original, _ in self.stack):
            while self.stack:
                original, mapped = self.stack.pop()
                self.parts.append("</" + mapped + ">")
                if original == tag:
                    break

    def handle_data(self, data):
        if self.hidden:
            return
        if self.markup:
            # XML source indentation is ordinary inline whitespace, not <br>.
            self.parts.append(escape(re.sub(r"\s+", " ", data)))
            return
        if not data.strip():
            if data and "\n" not in data:
                self.parts.append(" ")
            return
        lines = data.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.parts.append("<br>".join(escape(re.sub(r"[\t ]+", " ", line)) for line in lines))

    def result(self):
        self.close()
        while self.stack:
            _, mapped = self.stack.pop()
            self.parts.append("</" + mapped + ">")
        html = "".join(self.parts)
        if self.markup:
            # Pretty-printed inline tags also leave spaces inside parentheses
            # and before superscripts, e.g. "( <em>rice</em> )" and "Na <sup>+".
            html = re.sub(r"([\(\[]) +", r"\1", html)
            html = re.sub(r" +([,\)\]])", r"\1", html)
            html = re.sub(r" +(?=<(?:sup|sub)>)", "", html)
            html = re.sub(r"(<(?:sup|sub)>) +", r"\1", html)
            html = re.sub(r" +(?=</(?:sup|sub)>)", "", html)
        # A JATS paragraph that only wraps a list must not leave an empty HTML p.
        return re.sub(r"<p>\s*</p>", "", html).strip()


def abstract_html(value):
    if not isinstance(value, str) or len(value) > 100000 or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValueError("Invalid public abstract")
    parser = AbstractHTML(markup=bool(re.search(r"</?[A-Za-z][\w:.-]*(?:\s[^<>]*)?/?>", value)))
    parser.feed(value)
    return parser.result()


def description(abstract, journal, link):
    body = abstract_html(abstract)
    return (DESCRIPTION_PREFIX + ("<div>" + body + "</div>" if body else "")
            + "<p>Journal: " + escape(plain(journal) or "Not provided") + "</p>"
            + '<p>Original article: <a href="' + escape(link, quote=True) + '">'
            + escape(link) + "</a></p></div>")


def delivery_description(paper, journal, link):
    if not paper.get("abstract_zh"):
        return description(paper["abstract_en"], journal, link)
    return ('<div class="zotwatch-rss-description-v5"><h3>Abstract</h3><div>'
        + abstract_html(paper["abstract_en"]) + '</div><h3>摘要</h3><div>'
        + abstract_html(paper["abstract_zh"]) + '</div><p>Journal: '
        + escape(plain(journal) or "Not provided") + '</p><p>Original article: <a href="'
        + escape(link, quote=True) + '">' + escape(link) + '</a></p></div>')


def valid_public_abstract(value):
    """Same accepted text checks as metadata enrichment; keep safe source markup."""
    text = plain(value)
    return bool(text) and len(text) <= 6000 and not re.search(
        r"\b(author contributions?|conflict of interest|publisher.s note|"
        r"data availability statement|the author confirms being the sole contributor)\b",
        text, re.I) and not (len(text) < 600 and re.search(r"[,;].*https?:", text, re.I))


def public_metadata(guid):
    journal = ""
    if guid.startswith("urn:doi:"):
        doi = guid[8:]
        # Public exact-DOI fallback matches the private enrichment priority.
        try:
            metadata = json.loads(request("https://api.crossref.org/works/" + quote(doi, safe="")))["message"]
            if metadata["DOI"].lower() != doi:
                raise ValueError("Public metadata identity mismatch")
            journal = plain(next(iter(metadata.get("container-title") or []), ""))
            abstract = metadata.get("abstract", "")
            if valid_public_abstract(abstract):
                return {"abstract": abstract, "journal": journal}
        except (OSError, ValueError, KeyError):
            pass
        try:
            url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=" + quote('DOI:"'+doi+'"') + "&format=json&resultType=core&pageSize=10"
            metadata = json.loads(request(url))
            for row in metadata.get("resultList", {}).get("result", []):
                if (row.get("doi") or "").lower() == doi:
                    journal = journal or plain(((row.get("journalInfo") or {}).get("journal") or {}).get("title", ""))
                    abstract = row.get("abstractText", "")
                    if valid_public_abstract(abstract):
                        return {"abstract": abstract, "journal": journal}
        except (OSError, ValueError, KeyError):
            pass
        try:
            url = "https://api.openalex.org/works/https://doi.org/" + quote(doi, safe="")
            if os.getenv("OPENALEX_API_KEY"):
                url += "?api_key=" + quote(os.environ["OPENALEX_API_KEY"], safe="")
            metadata = json.loads(request(url))
            if (metadata.get("doi") or "").lower().removeprefix("https://doi.org/") != doi:
                raise ValueError("Public metadata identity mismatch")
            words = {}
            for word, positions in (metadata.get("abstract_inverted_index") or {}).items():
                for position in positions:
                    if type(position) is not int or not 0 <= position < 50000 or position in words:
                        raise ValueError("Invalid public abstract index")
                    words[position] = word
            journal = journal or plain(((metadata.get("primary_location") or {}).get("source") or {}).get("display_name", ""))
            abstract = " ".join(words[i] for i in sorted(words))
            return {"abstract": abstract if valid_public_abstract(abstract) else "", "journal": journal}
        except (OSError, ValueError, KeyError, TypeError):
            return {"abstract": "", "journal": journal}
    identifier = guid[10:]
    root = ET.fromstring(request("https://export.arxiv.org/api/query?id_list=" + quote(identifier, safe="")))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None or not re.search(re.escape(identifier) + r"(?:v\d+)?$", entry.findtext("a:id", "", ns)):
        raise ValueError("Public metadata identity mismatch")
    abstract = entry.findtext("a:summary", "", ns)
    return {"abstract": abstract if valid_public_abstract(abstract) else "", "journal": "arXiv"}


def public_abstract(guid):
    """Legacy plain-text accessor; RSS rendering uses the original public markup."""
    return plain(public_metadata(guid)["abstract"])


def read_feed(data):
    if data is None:
        return {}
    if len(data) > 16 * 1024 * 1024 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Invalid existing feed")
    root = ET.fromstring(data)
    channel = root.find("channel")
    if root.tag != "rss" or root.get("version") != "2.0" or channel is None:
        raise ValueError("Invalid existing feed")
    items = {}
    for item in channel.findall("item"):
        if len(item) != 5 or {c.tag for c in item} != ITEM_FIELDS:
            raise ValueError("Existing feed is outside RSS allowlist")
        row = {c.tag: c.text or "" for c in item}
        guid = row["guid"]
        expected = identity({"work_key": guid[8:] if guid.startswith("urn:doi:") else "arxiv:" + guid[10:]})
        if expected != (guid, row["link"]) or guid in items:
            raise ValueError("Invalid/duplicate existing GUID")
        if parsedate_to_datetime(row["pubDate"]).tzinfo is None:
            raise ValueError("Invalid existing publication date")
        items[guid] = row
    return items


def previous_metadata(value):
    """Separate our versioned description's body/footer during a format upgrade."""
    wrapper = re.match(r'<div class="zotwatch-rss-description-v[23]">', value)
    if not wrapper:
        return value, ""
    # Match the final generated footer, not labels that might occur in the body.
    footer = re.search(r'<p>(?:期刊来源：|Journal: )(.*?)</p>'
                       r'<p>(?:原文链接：|Original article: )<a href="[^"]*">[^<]*</a></p></div>$', value, re.S)
    if not footer:
        raise ValueError("Invalid previous RSS description")
    body = value[wrapper.end():footer.start()]
    if body.startswith("<div>") and body.endswith("</div>"):
        body = body[5:-6]
    journal = plain(footer.group(1))
    return body, "" if journal in {"未提供", "Not provided"} else journal


def merge_feed(final, old, feed_url, now=None, resolve=public_metadata):
    """Upgrade old descriptions once; keep GUIDs and first RSS publication dates."""
    if final is None or final.get("status") != "succeeded" or final.get("command") != "watch" or not final["recommendations"]:
        return old, 0
    items = read_feed(old)
    fresh = {}
    reformatted = False
    venues = {identity(row)[0]: row.get("venue", "") for row in final["recommendations"]}
    delivery = {identity({"work_key": key})[0]: paper for key, paper in final.get("_staging_ai", {}).items()}
    for guid, item in items.items():
        if not item["description"].startswith((DESCRIPTION_PREFIX, '<div class="zotwatch-rss-description-v5">')):
            metadata = resolve(guid)
            if isinstance(metadata, str):
                metadata = {"abstract": metadata, "journal": ""}
            # Preserve the previous abstract if the public provider is unavailable,
            # without nesting its already-generated journal/link footer.
            previous, previous_journal = previous_metadata(item["description"])
            item["description"] = description(metadata["abstract"] or previous,
                                              metadata["journal"] or venues.get(guid, "") or previous_journal, item["link"])
            reformatted = True
    stamp = format_datetime(now or datetime.now(timezone.utc), usegmt=True)
    for row in final["recommendations"]:
        guid, link = identity(row)
        if guid in delivery:
            paper = delivery[guid]
            rendered = dict(title=plain(row["title"]) + (" | " + plain(paper["title_zh"]) if paper.get("title_zh") else ""),
                link=link, guid=guid, pubDate=items[guid]["pubDate"] if guid in items else stamp,
                description=delivery_description(paper, venues[guid], link))
            if guid in items:
                if items[guid] != rendered:
                    items[guid] = rendered
                    reformatted = True
            else:
                fresh[guid] = rendered
            continue
        if guid not in items and guid not in fresh:
            title = plain(row["title"])
            if not title:
                raise ValueError("Empty public title")
            # Private final v2/v3 have no abstract. Resolve only selected public IDs.
            metadata = resolve(guid)
            if isinstance(metadata, str):
                metadata = {"abstract": metadata, "journal": ""}
            fresh[guid] = dict(title=title, link=link, guid=guid, pubDate=stamp,
                              description=description(metadata["abstract"], metadata["journal"] or venues[guid], link))
    if not fresh and not reformatted:
        return old, 0
    items.update(fresh)
    selected = sorted(items.values(), key=lambda row: (parsedate_to_datetime(row["pubDate"]), row["guid"]), reverse=True)[:100]
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    for field, value in (("title", "ZotWatch RSS"), ("link", feed_url),
                         ("description", "Public metadata of recommended papers.")):
        ET.SubElement(channel, field).text = value
    for row in selected:
        item = ET.SubElement(channel, "item")
        for field in ("title", "link", "guid", "pubDate", "description"):
            child = ET.SubElement(item, field)
            if field == "guid":
                child.set("isPermaLink", "false")
            child.text = row[field]
    ET.indent(root, space="  ")
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
    read_feed(data)
    return data, len(fresh)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def publish(final, target, destination, feed_url):
    if final is None or not final["recommendations"]:
        return {"changed": False, "new_items": 0, "reason": "no_successful_recommendations"}
    key = os.environ.get("RSS_PAGES_DEPLOY_KEY", "")
    source = os.environ["GITHUB_REPOSITORY"]
    if target.lower() != source.lower() and not key:
        raise ValueError("Separate RSS repository requires scoped deploy key")
    if key:
        keyfile = destination.parent / "rss-deploy-key"
        keyfile.write_text(key + "\n")
        keyfile.chmod(0o600)
        hosts = destination.parent / "rss-known-hosts"
        metadata = json.loads(request("https://api.github.com/meta"))
        hosts.write_text("".join("github.com " + k + "\n" for k in metadata["ssh_keys"]))
        # Paths are local generated runner paths, shell-quoted for Git's SSH hook.
        import shlex
        os.environ["GIT_SSH_COMMAND"] = ("ssh -i " + shlex.quote(str(keyfile))
            + " -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="
            + shlex.quote(str(hosts)))
        remote = f"git@github.com:{target}.git"
    else:
        askpass = destination.parent / "rss-askpass"
        askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s" x-access-token;; *) printf "%s" "$GITHUB_TOKEN";; esac\n')
        askpass.chmod(0o700)
        os.environ["GIT_ASKPASS"] = str(askpass)
        os.environ["GIT_TERMINAL_PROMPT"] = "0"
        remote = f"https://github.com/{target}.git"
    if destination.exists():
        raise ValueError("Pages checkout must be fresh")
    head = git("ls-remote", "--heads", remote, "refs/heads/gh-pages")
    if head:
        git("clone", "--depth=1", "--single-branch", "--branch=gh-pages", remote, str(destination))
        files = set(git("ls-files", cwd=destination).splitlines())
        if files != {"feed.xml", "index.html"}:
            raise ValueError("Pages branch must contain only RSS allowlisted files")
        if any((destination / name).is_symlink() for name in files):
            raise ValueError("Pages symlink rejected")
        old = (destination / "feed.xml").read_bytes()
    else:
        destination.mkdir()
        git("init", "--initial-branch=gh-pages", cwd=destination)
        git("remote", "add", "origin", remote, cwd=destination)
        old = None
    data, count = merge_feed(final, old, feed_url)
    if data == old:
        return {"changed": False, "new_items": 0, "items": len(read_feed(old))}
    (destination / "feed.xml").write_bytes(data)
    (destination / "index.html").write_text(INDEX)
    git("add", "--", "feed.xml", "index.html", cwd=destination)
    git("-c", "user.name=ZotWatch RSS", "-c", "user.email=rss@users.noreply.github.com",
        "commit", "-m", "Update public paper RSS", cwd=destination)
    # Ordinary fast-forward push: a concurrent publication cannot overwrite us.
    git("push", "origin", "HEAD:refs/heads/gh-pages", cwd=destination)
    return {"changed": True, "new_items": count, "items": len(read_feed(data)),
            "feed_sha256": hashlib.sha256(data).hexdigest(), "commit": git("rev-parse", "HEAD", cwd=destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    source = os.environ["GITHUB_REPOSITORY"]
    if not REPOSITORY.fullmatch(source) or not REPOSITORY.fullmatch(args.target) or not re.fullmatch(r"[1-9]\d*", args.run_id):
        raise ValueError("Invalid repository or run ID")
    owner, name = args.target.split("/")
    feed_url = f"https://{owner.lower()}.github.io/{name}/feed.xml"
    final = load_final(source, args.run_id)
    print(json.dumps(publish(final, args.target, args.destination, feed_url), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never log an artifact, response, Git error, credential or private field.
        print("RSS publication failed; existing remote feed preserved.", file=sys.stderr)
        sys.exit(1)
