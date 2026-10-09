"""Fixed v1 maximum cosine ranking and two-pass primary-center selection."""
from collections import Counter
from .recall_integration import work_key

TOP_N = 20
FIRST_PASS_PER_CENTER = 3


def rank(candidates, decisions):
    matches = {r["candidate_id"]: r["best_match"] for r in decisions if r["recalled"]}
    unique = {}
    for c in candidates:
        key = work_key(c)
        unique.setdefault(key, c)
    rows = []
    for key, c in unique.items():
        match = matches[key]
        rows.append(dict(work_key=key, title=c.title, url=c.url,
            primary_center_id=match["interest_id"], center_cosine=match["center_cosine"],
            score=match["center_cosine"], source=c.source, identifier=c.identifier,
            doi=c.doi, venue=c.venue, published=c.extra.get("publication_date") or c.published.isoformat()))
    return sorted(rows, key=lambda r: (-r["center_cosine"], r["work_key"]))


def select(rows, *, limit=TOP_N):
    ordered = sorted(rows, key=lambda r: (-r["center_cosine"], r["work_key"]))
    chosen, rest, counts, seen = [], [], Counter(), set()
    for row in ordered:
        if row["work_key"] in seen:
            continue
        seen.add(row["work_key"])
        center = row["primary_center_id"]
        if counts[center] < FIRST_PASS_PER_CENTER:
            chosen.append(row)
            counts[center] += 1
        else:
            rest.append(row)
    ordered = chosen + rest
    return ordered if limit is None else ordered[:limit]
