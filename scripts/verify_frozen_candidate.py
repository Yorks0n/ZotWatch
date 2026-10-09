"""Read-only exact formation verification of the frozen P5B3 candidate."""
import argparse
from pathlib import Path
import sys

import numpy as np
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zotwatch.interests.frozen_candidate import load_frozen, digest, semantic_text
from zotwatch.interests.interest_formation import form_interest_model


def verify(directory):
    snapshot, inputs, vectors = load_frozen(directory)
    with threadpool_limits(limits=1):
        repeated = form_interest_model(vectors, inputs["keys"], inputs["titles"], inputs["abstracts"], dois=inputs["dois"])
    assert repeated == snapshot["formation"]
    texts = ["\n".join(part.strip() for part in (title, abstract or "") if part.strip())
        for title, abstract in zip(inputs["titles"], inputs["abstracts"])]
    from hashlib import sha256
    import json
    assert sha256(json.dumps(list(zip(inputs["keys"], texts)), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest() == snapshot["benchmark_input_sha256"]
    for row in repeated["eligible_input_rows"]:
        assert semantic_text(inputs["titles"][row], inputs["abstracts"][row]) == texts[row]
    q = repeated["qualification"]
    published = {c["id"]: c for c in q["clusters"] if c["published"]}
    assert len(snapshot["centers"]) == len(published)
    assert len({c["interest_id"] for c in snapshot["centers"]}) == len(published)
    for center in snapshot["centers"]:
        group = published[center["raw_cluster_alias"]]
        assert center["centroid"] == group["trimmed_centroid"]
        assert center["representative_items"] == [rep["key"] for rep in group["representatives"]]
        assert center["distinct_strong_works"] == group["distinct_strong_works"] >= 10
        assert center["member_keys"] == sorted(work["key"] for work in center["strong_works"])
        assert set(center["member_keys"]) == {rep["key"] for rep in q["members"]
            if rep["formal_label"] == group["raw_label"]}
    print(f"PASS: {snapshot['model_revision']} / exact snapshot {digest(snapshot)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capsule", required=True)
    verify(parser.parse_args().capsule)
