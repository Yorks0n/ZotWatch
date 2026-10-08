"""Opt-in private observation of the existing recommendation gates."""
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path

GROUPS = ("Bioinformatics", "PNAS", "plant_journals", "other")
PLANT_PREFIXES = ("10.3389/fpls.", "10.1093/plphys/", "10.1111/nph.",
                  "10.1111/tpj.", "10.1093/aob/", "10.1093/jxb/", "10.1093/plcell/")


def key(work):
    return work.doi or f"{work.source}:{work.identifier}"


def group(work):
    doi = (work.doi or "").lower()
    if doi.startswith("10.1093/bioinformatics/"):
        return "Bioinformatics"
    if doi.startswith("10.1073/pnas."):
        return "PNAS"
    if doi.startswith(PLANT_PREFIXES):
        return "plant_journals"
    return "other"


def counts(works):
    works = list(works)
    grouped = Counter(group(w) for w in works)
    return {"total": len(works), "groups": {g: grouped[g] for g in GROUPS},
            "sources": dict(Counter(w.source for w in works))}


class FilterDiagnostic:
    def __init__(self, run_id, profile):
        self.data = {"schema_name": "zotwatch-private-filter-diagnostic", "schema_version": 1,
                     "run_id": run_id, "stages": {}, "candidates": {}, "crossref_dates": [],
                     "topics": [{"id": t.id, "status": t.status} for t in (profile.interests if profile else [])],
                     "ranking_topic_ids": [],
                     "active_topic_count": sum(t.status == "active" for t in (profile.interests if profile else []))}

    def collected(self, works):
        self.data["collected"] = counts(works)
        unique = {}
        for work in works:
            unique.setdefault(key(work), work)
            if work.extra.get("source") == "top_venue":
                self.data["crossref_dates"].append({"work_key": key(work), "group": group(work),
                    "first_occurrence_selected": unique[key(work)] is work,
                    "created_date": work.extra.get("doi_created_at") or (work.published.isoformat() if work.published else None),
                    "publication_dates": work.extra.get("diagnostic_publication_dates", {})})
        self.works = unique
        for ident, work in unique.items():
            self.data["candidates"][ident] = {"title": work.title, "venue": work.venue,
                "source": work.source, "fetch_path": work.extra.get("source", "public_api"),
                "group": group(work), "published": work.extra.get("publication_date") or (work.published.isoformat() if work.published else None),
                **{k:work.extra[k] for k in ("publication_precision","publication_source","doi_created_at","abstract_source","abstract_status","fetched_at","metadata_cache_hit") if k in work.extra}}
        return list(unique.values())

    def reject(self, stage, work, reason, **details):
        self.data["candidates"][key(work)].update(
            {"eliminated_stage": stage, "reason": reason, **details})

    def stage(self, name, before, after):
        after_keys = {key(w) for w in after}
        rejected = [w for w in before if key(w) not in after_keys]
        self.data["stages"][name] = {"input": counts(before), "passed": counts(after),
                                     "eliminated": counts(rejected)}
        for work in rejected:
            if "eliminated_stage" not in self.data["candidates"][key(work)]:
                self.reject(name, work, "stage_rejected")

    def topic(self, work, similarities, matched_id):
        row = self.data["candidates"][key(work)]
        row["topic_similarities"] = similarities
        row["below_0_35"] = not any(s >= 0.35 for s in similarities.values())
        row["matched_topic_id"] = matched_id
        if matched_id is None:
            self.reject("confirmed_topic_admission", work, "below_0_35_all_active_topics")
        self.data["ranking_topic_ids"] = list(similarities)

    def dates(self, cutoff):
        self.data["seven_day_cutoff"] = cutoff.isoformat()
        for row in self.data["crossref_dates"]:
            created = datetime.fromisoformat(row["created_date"]) if row["created_date"] else None
            row["created_admitted"] = bool(created and created >= cutoff)
            comparisons = {}
            for field, value in row["publication_dates"].items():
                parts = value.get("date-parts", [[]])[0] if isinstance(value, dict) else []
                if len(parts) != 3:
                    comparisons[field] = {"recoverable": False, "date_parts": parts}
                    continue
                date = datetime(*parts, tzinfo=timezone.utc)
                # Crossref calendar dates have no time: expose midnight convention
                # and identify cutoff-day uncertainty rather than invent a time.
                comparisons[field] = {"recoverable": True, "date": date.date().isoformat(),
                    "midnight_admitted": date >= cutoff,
                    "cutoff_day_ambiguous": date.date() == cutoff.date(),
                    "changes_midnight_verdict": (date >= cutoff) != row["created_admitted"]}
            row["comparison"] = comparisons

    def finish(self, state, status):
        self.data["status"] = status
        if status == "succeeded":
            for row in self.data["candidates"].values():
                row["final_recommended"] = "eliminated_stage" not in row
        target = Path(state) / "runs" / f"filter-diagnostic-{self.data['run_id']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        with open(temporary, "w", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            json.dump(self.data, stream, ensure_ascii=False)
        temporary.replace(target)
