"""Exact-DOI enrichment and an independent bounded private metadata cache."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote
import requests
from .publication_dates import crossref_date, date_datetime

NAME = "metadata-cache-v1.json"
MAX_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 5000
DOI = re.compile(r"10\.\d{4,9}/[^\s?#]+", re.I)


def normalize_doi(value):
    if not isinstance(value, str):
        return None
    value = unquote(value.strip()).lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value if DOI.fullmatch(value) else None


def clean(value):
    if not isinstance(value, str) or len(value) > 200000:
        return None
    value = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())
    return value if value and not any(ord(c) < 32 for c in value) else None


def inverted_text(value):
    if not isinstance(value, dict):
        return None
    words = {}
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            return None
        for position in positions:
            if type(position) is not int or not 0 <= position < 50000 or position in words:
                return None
            words[position] = word
    return clean(" ".join(words[i] for i in sorted(words)))


class Cache:
    def __init__(self, state, repository_id):
        self.path = Path(state) / NAME
        self.repository_id = repository_id
        self.entries = {}
        if self.path.exists():
            self.entries = self.validate(self.path.read_bytes(), repository_id)["entries"]

    @staticmethod
    def validate(data, repository_id):
        if len(data) > MAX_BYTES:
            raise ValueError("Metadata cache size exceeded")
        value = json.loads(data)
        if set(value) != {"schema_name","schema_version","workspace_repository_id","entries"} or value["schema_name"] != "zotwatch-metadata-cache" or value["schema_version"] != 1 or value["workspace_repository_id"] != repository_id:
            raise ValueError("Metadata cache scope/schema mismatch")
        entries = value["entries"]
        if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES:
            raise ValueError("Metadata cache entry budget exceeded")
        for doi, entry in entries.items():
            if normalize_doi(doi) != doi or not isinstance(entry, dict) or entry.get("doi") != doi:
                raise ValueError("Metadata cache DOI mismatch")
            fields = {"doi","abstract","abstract_source","abstract_status","fetched_at","expires_at","publication","attempts","unavailable_providers","crossref_date_status"}
            if set(entry) != fields or not isinstance(entry["publication"], dict) or not isinstance(entry["attempts"], list):
                raise ValueError("Invalid metadata cache entry schema")
            if entry.get("abstract_status") not in ("available", "not_found", "unavailable"):
                raise ValueError("Invalid abstract status")
            for key in ("fetched_at", "expires_at"):
                if not isinstance(entry[key], str) or datetime.fromisoformat(entry[key]).tzinfo is None:
                    raise ValueError("Metadata cache timestamp not aware")
            if entry["abstract_status"] == "available" and not clean(entry.get("abstract")):
                raise ValueError("Invalid cached abstract")
        return value

    def get(self, doi, now):
        entry = self.entries.get(doi)
        return entry if entry and datetime.fromisoformat(entry["expires_at"]) > now else None

    def save(self):
        # Called under the normal StateCoordinator lease; no second lock required.
        self.entries = dict(sorted(self.entries.items(), key=lambda kv:(kv[1]["fetched_at"],kv[0]), reverse=True)[:MAX_ENTRIES])
        while True:
            data = json.dumps(dict(schema_name="zotwatch-metadata-cache", schema_version=1,
                workspace_repository_id=self.repository_id, entries=self.entries), sort_keys=True, ensure_ascii=False).encode()
            if len(data) <= MAX_BYTES:
                break
            self.entries.pop(next(reversed(self.entries)))
        self.validate(data, self.repository_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "wb", opener=lambda p,f: os.open(p,f,0o600)) as stream:
            stream.write(data)
        tmp.replace(self.path)


class Providers:
    def get(self, url, params=None):
        response = requests.get(url, params=params, headers={"User-Agent":"ZotWatch-metadata/1.0"}, timeout=(5,15))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        if len(response.content) > 2 * 1024 * 1024:
            raise ValueError("Metadata response too large")
        return response.json()

    def crossref(self, doi):
        value = self.get("https://api.crossref.org/works/" + quote(doi,safe=""))
        if value is None:
            return {}
        value = value["message"]
        if normalize_doi(value.get("DOI")) != doi:
            raise ValueError("Crossref DOI mismatch")
        return value

    def europe_pmc(self, doi):
        value = self.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", dict(query='DOI:"'+doi+'"',format="json",resultType="core",pageSize=10))
        rows = (value or {}).get("resultList", {}).get("result", [])
        for row in rows:
            if normalize_doi(row.get("doi")) == doi and clean(row.get("abstractText")):
                return clean(row["abstractText"])
        return None

    def openalex(self, doi):
        params = {"api_key":os.environ["OPENALEX_API_KEY"]} if os.getenv("OPENALEX_API_KEY") else None
        row = self.get("https://api.openalex.org/works/https://doi.org/" + quote(doi,safe=""), params)
        if row is None:
            return None
        if normalize_doi(row.get("doi")) != doi:
            raise ValueError("OpenAlex DOI mismatch")
        return inverted_text(row.get("abstract_inverted_index"))


def resolve(doi, now, providers, metadata=None, need_abstract=True):
    errors, attempts = [], []
    if metadata is None:
        try:
            metadata = providers.crossref(doi)
            attempts.append("crossref")
        except Exception:
            metadata = {}
            errors.append("crossref")
    abstract, source = clean(metadata.get("abstract")), "crossref"
    if need_abstract and not abstract:
        for name in ("europe_pmc", "openalex"):
            attempts.append(name)
            try:
                abstract = getattr(providers, name)(doi)
            except Exception:
                errors.append(name)
            if abstract:
                source = name
                break
    status = "available" if abstract else "unavailable" if errors or not need_abstract else "not_found"
    ttl = timedelta(hours=1) if "crossref" in errors else timedelta(days=30) if status == "available" else timedelta(days=7) if status == "not_found" else timedelta(hours=1)
    return dict(doi=doi, abstract=abstract, abstract_source=source if abstract else None,
        abstract_status=status, fetched_at=now.isoformat(), expires_at=(now+ttl).isoformat(),
        publication=crossref_date(metadata), attempts=attempts, unavailable_providers=errors,
        crossref_date_status="unavailable" if "crossref" in errors else "resolved")


def enrich(candidates, cache, now=None, providers=None):
    now, providers = now or datetime.now(timezone.utc), providers or Providers()
    grouped, seeds = {}, {}
    for candidate in candidates:
        doi = normalize_doi(candidate.doi)
        if not doi:
            continue
        grouped.setdefault(doi, []).append(candidate)
        metadata = candidate.extra.get("crossref_metadata")
        if isinstance(metadata, dict) and normalize_doi(metadata.get("DOI")) == doi:
            seeds[doi] = metadata
    stats, records, pending = Counter(), {}, []
    for doi, group in grouped.items():
        entry = cache.get(doi, now)
        need_abstract = any(not clean(c.abstract) for c in group)
        if not need_abstract and not any(c.source == "crossref" for c in group):
            continue
        # An entry fetched only for date metadata must not suppress later enrichment.
        if entry and (entry.get("abstract") or entry["abstract_status"] == "not_found" or entry.get("attempts") != ["crossref"]):
            records[doi] = (entry, True)
            stats["persistent_cache_hits"] += 1
        elif entry and not need_abstract:
            records[doi] = (entry, True)
            stats["persistent_cache_hits"] += 1
        else:
            pending.append((doi, need_abstract))
    def fetch(job):
        doi, needed = job
        try:
            entry = resolve(doi, now, providers, seeds.get(doi), needed)
        except Exception:
            entry = dict(doi=doi,abstract=None,abstract_source=None,abstract_status="unavailable",
                fetched_at=now.isoformat(),expires_at=(now+timedelta(hours=1)).isoformat(),
                publication=crossref_date({}),attempts=[],unavailable_providers=["invalid_metadata"],crossref_date_status="unavailable")
        if not needed and not entry["abstract"]:
            entry.update(abstract=clean(grouped[doi][0].abstract), abstract_source=grouped[doi][0].source,
                abstract_status="available", expires_at=(now+(timedelta(hours=1) if entry["crossref_date_status"] == "unavailable" else timedelta(days=30))).isoformat())
        return doi, entry
    with ThreadPoolExecutor(max_workers=4) as pool:
        for doi, entry in pool.map(fetch, pending):
            records[doi] = (entry, False)
            cache.entries[doi] = entry
            stats["resolved_dois"] += 1
    evidence = []
    for doi, group in grouped.items():
        if doi not in records:
            continue
        entry, hit = records[doi]
        for candidate in group:
            had_abstract = bool(clean(candidate.abstract))
            if had_abstract:
                candidate.extra.update(abstract_source=candidate.extra.get("abstract_source") or candidate.source,
                    abstract_status="available", fetched_at=entry["fetched_at"])
                stats["preserved_abstracts"] += 1
            else:
                candidate.abstract = entry["abstract"]
                candidate.extra.update({k:entry[k] for k in ("abstract_source","abstract_status","fetched_at")})
                if candidate.abstract:
                    stats["enriched_abstracts"] += 1
            candidate.extra["metadata_cache_hit"] = hit
            if candidate.source == "crossref":
                candidate.extra.update(entry["publication"])
                candidate.published = date_datetime({"published":{"date-parts":[[int(v) for v in entry["publication"]["publication_date"].split("-")]]}}) if entry["publication"]["publication_date"] else None
            evidence.append(dict(doi=doi, source=candidate.source, originally_missing=not had_abstract,
                cache_hit=hit, abstract_source=candidate.extra.get("abstract_source"),
                abstract_status=candidate.extra.get("abstract_status"), fetched_at=entry["fetched_at"],
                publication=entry["publication"] if candidate.source == "crossref" else None))
    cache.save()
    return dict(schema_name="zotwatch-metadata-enrichment", schema_version=1, counts=dict(stats), candidates=evidence)
