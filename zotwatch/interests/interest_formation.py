"""Eligible-only local interest formation; no publication or title-only association."""
from __future__ import annotations

import numpy as np

from .auto_spherical import auto_spherical
from .membership_qualification import MAX_ITEMS, qualify_membership

ELIGIBILITY_POLICY_VERSION = "abstract-required-v2"


def form_interest_model(vectors, keys, titles, abstracts, *, dois=None):
    """Use exactly the fixed auto-K/qualification/trim pipeline on eligible rows.

    Full-input vectors are benchmark evidence. Ineligible rows never reach the
    fitter, K size calculation, work matching, centroids or membership scores.
    Raw labels/row indices in qualification refer only to eligible_input_rows.
    """
    x = np.asarray(vectors)
    n = len(keys)
    dois = [None] * n if dois is None else list(dois)
    if x.ndim != 2 or x.shape[0] != n or x.shape[1] < 1 or n > MAX_ITEMS:
        raise ValueError("Invalid interest formation input or budget")
    if any(len(values) != n for values in (titles, abstracts, dois)):
        raise ValueError("Interest formation metadata alignment mismatch")
    if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != n:
        raise ValueError("Interest formation requires unique text keys")
    if any(value is not None and not isinstance(value, str) for values in (titles, abstracts, dois) for value in values):
        raise ValueError("Interest formation metadata must be text")
    eligible = [i for i, abstract in enumerate(abstracts) if (abstract or "").strip()]
    selected = [[values[i] for i in eligible] for values in (keys, titles, abstracts, dois)]
    raw, selector = auto_spherical(x[eligible])
    qualified = qualify_membership(x[eligible], raw, *selected[:3], dois=selected[3])
    excluded = [{"input_row": i, "key": keys[i], "status": "ineligible_for_interest_model",
        "reason": "insufficient_semantic_text", "raw_label": None, "formal_label": None}
        for i, abstract in enumerate(abstracts) if not (abstract or "").strip()]
    summary = qualified["summary"]
    return {"eligibility_policy_version": ELIGIBILITY_POLICY_VERSION,
        "summary": {"input_count": n, "eligible_count": len(eligible), "ineligible_count": len(excluded),
            "raw_cluster_count": summary["raw_cluster_count"], "formal_center_count": summary["formal_center_count"],
            "strong_count": summary["strong_count"], "strong_coverage": summary["strong_coverage"],
            "weak_noise_count": summary["weak_noise_count"], "weak_noise_fraction": summary["weak_noise_fraction"],
            "total_input_strong_fraction": summary["strong_count"] / n if n else 0.},
        "eligible_input_rows": eligible, "eligible_keys": selected[0], "ineligible_records": excluded,
        "selector": selector, "qualification": qualified}
