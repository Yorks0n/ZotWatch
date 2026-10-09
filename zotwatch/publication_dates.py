"""Publication provenance and UTC calendar-day admission (latent-auto v1)."""
from datetime import date, datetime, timedelta, timezone

CROSSREF_PRIORITY = ("published-online", "published", "issued", "published-print")


def crossref_date(metadata):
    result = dict(publication_date=None, publication_precision="missing", publication_source=None,
                  doi_created_at=(metadata.get("created") or {}).get("date-time") if isinstance(metadata.get("created"), dict) else None)
    for field in CROSSREF_PRIORITY:
        raw = metadata.get(field)
        if not isinstance(raw, dict):
            continue
        parts = raw.get("date-parts", [[]])
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
            continue
        values = parts[0]
        if len(values) not in (1, 2, 3) or any(type(v) is not int for v in values):
            continue
        try:
            # Validate partial dates without publishing invented month/day/time.
            date(*values, *([1] * (3 - len(values))))
        except (ValueError, TypeError):
            continue
        result.update(publication_date="-".join(f"{v:04d}" if i == 0 else f"{v:02d}" for i,v in enumerate(values)),
                      publication_precision={1:"year",2:"month",3:"day"}[len(values)],
                      publication_source="crossref:" + field)
        break
    return result


def date_datetime(metadata):
    """Legacy CandidateWork adapter; precision remains in extra, final emits a date."""
    info = crossref_date(metadata)
    if info["publication_precision"] != "day":
        return None
    return datetime.combine(date.fromisoformat(info["publication_date"]), datetime.min.time(), timezone.utc)


def admission(candidates, now=None):
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    start = today - timedelta(days=6)
    passed, rejected = [], {}
    for candidate in candidates:
        info = candidate.extra
        value, precision = info.get("publication_date"), info.get("publication_precision")
        if precision is None and candidate.source != "crossref" and candidate.published:
            value = candidate.published.astimezone(timezone.utc).date().isoformat()
            precision = "day"
        if precision != "day" or not value:
            reason = "publication_date_missing" if precision in (None,"missing") else "publication_date_insufficient_precision"
        else:
            try:
                published = date.fromisoformat(value)
                reason = "publication_date_future" if published > today else "publication_date_outside_window" if published < start else None
            except ValueError:
                reason = "publication_date_invalid"
        if reason:
            from zotwatch.interests.recall_integration import work_key
            rejected[work_key(candidate)] = reason
        else:
            passed.append(candidate)
    return passed, dict(policy="utc-calendar-seven-days-v1", first_date=start.isoformat(),
                        last_date=today.isoformat(), inclusive=True, rejected=rejected)
