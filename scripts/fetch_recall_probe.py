"""One bounded public-paper cohort, never uploads library text or identifiers."""
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import sys

import requests

QUERIES = {
    "positive-single-cell": 'TITLE:"single-cell" AND (TITLE:plant OR TITLE:Arabidopsis OR TITLE:root)',
    "positive-crispr": 'TITLE:CRISPR AND (TITLE:plant OR TITLE:editing OR TITLE:screen)',
    "positive-nrt": 'TITLE:NRT2 OR (TITLE:nitrate AND TITLE:transporter)',
    "positive-salt": 'TITLE:halophyte AND (TITLE:salt OR TITLE:salinity OR TITLE:tolerance)',
    "positive-annotation": 'TITLE:genome AND TITLE:annotation',
    "negative-clinical-egfr": 'TITLE:osimertinib AND TITLE:clinical',
    "negative-melanoma": 'TITLE:melanoma AND TITLE:immunotherapy AND NOT TITLE:single-cell AND NOT TITLE:CRISPR',
    "negative-mosquito": 'TITLE:mosquito AND (TITLE:dengue OR TITLE:arbovirus) AND NOT TITLE:genome',
    "negative-mixed-method": '(TITLE:"recommender system" OR TITLE:"data visualization") AND (TITLE:clinical OR TITLE:healthcare)',
    "negative-dental": 'TITLE:dental AND TITLE:implant AND TITLE:clinical',
}


def fetch(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for category, query in QUERIES.items():
        params = {"query": f'({query}) AND HAS_ABSTRACT:Y AND FIRST_PDATE:[2024-01-01 TO 2026-09-30]',
            "format": "json", "resultType": "core", "pageSize": 6}
        response = requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params=params, timeout=40,
            headers={"User-Agent": "ZotWatch-local-recall-benchmark/1"})
        response.raise_for_status()
        payload = response.json()
        (output / (category + ".json")).write_text(json.dumps({"request_url": response.url,
            "fetched_at": datetime.now(timezone.utc).isoformat(), "response": payload}, ensure_ascii=False, indent=2))
        for rank, record in enumerate(payload["resultList"]["result"]):
            abstract = html.unescape(re.sub(r"<[^>]+>", " ", record.get("abstractText", ""))).strip()
            if abstract:
                records.append({"id": record["source"]+":"+record["id"], "title": html.unescape(record["title"]),
                    "abstract": abstract, "doi": record.get("doi"), "date": record.get("firstPublicationDate"),
                    "url": f'https://europepmc.org/article/{record["source"]}/{record["id"]}',
                    "sampling_category": category, "source_rank": rank,
                    "provisional_expected": category.startswith("positive-"),
                    "split": "calibration" if rank % 2 == 0 else "heldout"})
        print(category, len(payload["resultList"]["result"]), flush=True)
    (output / "unreviewed_candidates.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    fetch(sys.argv[1])
