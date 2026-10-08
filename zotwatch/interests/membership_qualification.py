"""Experimental, one-pass qualification of a frozen spherical raw partition.

No fitting, reassignment, embedding, persistence or semantic labeling occurs.
Only abstract-eligible rows may enter this stage; ineligible records are separate.
"""
from __future__ import annotations

from hashlib import sha256
import re
import unicodedata

import numpy as np

POLICY_VERSION = "membership-qualification-experimental-v1"
MIN_COSINE = 0.50
MIN_MARGIN = 0.02
MIN_DISTINCT_STRONG_WORKS = 10
MAX_ITEMS = 20000
MAX_RAW_CLUSTERS = 60


def _text(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def work_identities(keys, titles, abstracts, dois):
    """Conservative exact DOI/title union, not a fuzzy work-resolution service.

    Exact normalized titles can conservatively collapse unrelated generic titles.
    Missing DOI and differently spelled titles cannot be linked reliably here.
    """
    parent = list(range(len(keys)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, (title, abstract, doi) in enumerate(zip(titles, abstracts, dois)):
        normalized_doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", _text(doi))
        tokens = [("doi", normalized_doi)] if normalized_doi else []
        if _text(title):
            tokens.append(("title", _text(title)))
        elif _text(abstract):
            tokens.append(("abstract", _text(abstract)))
        for token in tokens:
            if token in seen:
                left, right = root(i), root(seen[token])
                parent[max(left, right)] = min(left, right)
            seen[token] = i
    groups = {}
    for i, key in enumerate(keys):
        groups.setdefault(root(i), []).append(key)
    return [sha256(min(groups[root(i)]).encode()).hexdigest() for i in range(len(keys))]


def document_title_probe(title):
    """Tiny diagnostic only: filename suffix and exact wrapper words at edges.

    Suggestions never replace titles, embeddings, eligibility or membership.
    No language inference or scientific-word stoplist is used.
    """
    original = " ".join((title or "").split())
    cleaned = re.sub(r"\.(?:pdf|docx?|pptx?)$", "", original, flags=re.I).strip()
    cleaned = re.sub(r"^(?:full[ -]text|pdf)\s*[:_-]\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*\[(?:pdf|full[ -]text)\]$", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"\s+(?:manual|documentation|tutorial|user['’]s guide)$", "", cleaned, flags=re.I).strip()
    return {"flag": "DOCUMENT_TITLE_WRAPPER_REVIEW" if cleaned != original else None,
        "suggested_title": cleaned if cleaned != original else None}


def _unit(vector):
    norm = np.linalg.norm(vector)
    if not np.isfinite(norm) or norm <= 1e-12:
        return None
    return vector / norm


def qualify_membership(vectors, raw_labels, keys, titles, abstracts, *, dois=None):
    """Freeze an abstract-only probe, gate once, trim once, then support-gate.

    Returned formal labels retain raw numeric cluster IDs; rejected rows use -1.
    Diagnostic cluster aliases C01... sort by first item key, not fitter labels.
    Strong coverage counts only members of centers with >=10 distinct strong works.
    Duplicates count once for support and representatives; all strong rows enter
    the normalized-mean centroid, matching the original spherical mean convention.
    """
    x = np.asarray(vectors, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] == 0:
        raise ValueError("Expected a vector matrix with positive dimension")
    labels = np.asarray(raw_labels)
    n = len(x)
    dois = [None] * n if dois is None else list(dois)
    if x.ndim != 2 or x.shape[1] == 0 or labels.shape != (n,) or labels.dtype.kind not in "iu" or np.any(labels < -1):
        raise ValueError("Invalid vectors or raw partition")
    if n > MAX_ITEMS or any(len(values) != n for values in (keys, titles, abstracts, dois)):
        raise ValueError("Membership input budget or metadata alignment mismatch")
    if any(not isinstance(key, str) or not key for key in keys) or any(value is not None and not isinstance(value, str) for values in (titles, abstracts, dois) for value in values):
        raise ValueError("Membership metadata must contain text values")
    if len(set(keys)) != n:
        raise ValueError("Membership key uniqueness mismatch")
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    if not np.isfinite(x).all() or not np.isfinite(norms).all() or np.any(norms <= 1e-12):
        raise ValueError("Invalid membership vectors")
    x = x / norms
    labels = labels.astype(np.int64, copy=True)
    has_abstract = np.array([bool((abstract or "").strip()) for abstract in abstracts], dtype=bool)
    if not has_abstract.all():
        raise ValueError("Title-only records are ineligible; filter before auto_spherical and qualification")
    # A title-only record cannot even bridge two work identities via metadata.
    eligible = np.flatnonzero(has_abstract)
    work_ids = [sha256(key.encode()).hexdigest() for key in keys]
    eligible_ids = work_identities(*[[values[row] for row in eligible] for values in (keys, titles, abstracts, dois)])
    for row, identity in zip(eligible, eligible_ids):
        work_ids[row] = identity
    order = sorted(set(labels) - {-1}, key=lambda label: min(keys[i] for i in np.flatnonzero(labels == label)))
    if len(order) > MAX_RAW_CLUSTERS:
        raise ValueError("Raw partition exceeds the 60-center qualification budget")
    groups, valid = [], []
    for index, label in enumerate(order):
        rows = np.flatnonzero(labels == label)
        eligible = rows[has_abstract[rows]]
        probe = _unit(x[eligible].mean(axis=0)) if len(eligible) else None
        group = {"id": f"C{index+1:02}", "raw_label": int(label), "raw_support": len(rows),
            "abstract_support": len(eligible), "rows": rows.tolist(), "probe_centroid": None if probe is None else probe.tolist()}
        groups.append(group)
        if probe is not None:
            valid.append(group)
    similarities = np.full(n, np.nan)
    margins = np.full(n, np.nan)
    probe_strong = np.zeros(n, dtype=bool)
    reasons = [[] for _ in range(n)]
    eligible_rows = np.flatnonzero(has_abstract & (labels >= 0))
    if valid:
        centers = np.array([group["probe_centroid"] for group in valid])
        scores = np.clip(x[eligible_rows] @ centers.T, -1, 1)
        positions = {group["raw_label"]: i for i, group in enumerate(valid)}
        for position, row in enumerate(eligible_rows):
            assigned = positions.get(int(labels[row]))
            if assigned is None:
                continue
            similarities[row] = scores[position, assigned]
            alternative = np.delete(scores[position], assigned)
            margins[row] = similarities[row] - alternative.max() if len(alternative) else 2.0
            probe_strong[row] = similarities[row] >= MIN_COSINE and margins[row] >= MIN_MARGIN
    for row in range(n):
        if labels[row] == -1:
            reasons[row].append("raw_noise")
        if not has_abstract[row]:
            reasons[row].append("no_abstract")
        elif labels[row] >= 0:
            if not np.isfinite(similarities[row]):
                reasons[row].append("undefined_probe_centroid")
            else:
                if similarities[row] < MIN_COSINE:
                    reasons[row].append("weak_cosine")
                if margins[row] < MIN_MARGIN:
                    reasons[row].append("ambiguous_margin")
    formal_labels = np.full(n, -1, dtype=np.int64)
    formal = []
    for group in groups:
        rows = np.array(group["rows"], dtype=int)
        strong = rows[probe_strong[rows]]
        distinct = len({work_ids[row] for row in strong})
        trimmed = _unit(x[strong].mean(axis=0)) if len(strong) else None
        publish = distinct >= MIN_DISTINCT_STRONG_WORKS and trimmed is not None
        group.update(probe_strong_support=len(strong), distinct_strong_works=distinct,
            published=publish, strong_support=len(strong) if publish else 0,
            weak_noise_support=len(rows) - (len(strong) if publish else 0),
            probe_strong_rows=strong.tolist(), trimmed_centroid=None if trimmed is None else trimmed.tolist(),
            flags=[], representatives=[], trimmed_cohesion=None, strong_cohesion_before=None)
        wrappers = [int(row) for row in rows if document_title_probe(titles[row])["flag"]]
        group["document_title_probe_rows"] = wrappers
        if wrappers:
            group["flags"].append("DOCUMENT_TITLE_WRAPPER_REVIEW")
        if not publish:
            group["flags"].append("INSUFFICIENT_DISTINCT_STRONG_WORKS" if distinct < MIN_DISTINCT_STRONG_WORKS else "UNDEFINED_TRIMMED_CENTROID")
            for row in strong:
                reasons[row].append("unpublished_raw_cluster")
            continue
        formal_labels[strong] = group["raw_label"]
        cosines = np.clip(x[strong] @ trimmed, -1, 1)
        group["trimmed_cohesion"] = float(cosines.mean())
        group["trimmed_median_cohesion"] = float(np.median(cosines))
        group["strong_cohesion_before"] = float(similarities[strong].mean())
        group["probe_to_trimmed_cosine"] = float(np.clip(np.asarray(group["probe_centroid"]) @ trimmed, -1, 1))
        candidates = sorted(zip(strong, cosines), key=lambda pair: (-pair[1], keys[pair[0]]))
        represented = set()
        for row, cosine in candidates:
            if work_ids[row] in represented:
                continue
            represented.add(work_ids[row])
            group["representatives"].append({"row": int(row), "key": keys[row], "title": titles[row],
                "trimmed_cosine": float(cosine), "probe_cosine": float(similarities[row]), "probe_margin": float(margins[row])})
            if len(represented) == 5:
                break
        if group["trimmed_cohesion"] < .60:
            group["flags"].append("LOW_TRIMMED_COHESION_REVIEW")
        if len(strong) >= max(.15 * n, 3 * n / max(1, len(order))):
            group["flags"].append("GIANT_REVIEW")
        formal.append(group)
    if formal:
        centers = np.array([group["trimmed_centroid"] for group in formal])
        pairwise = np.clip(centers @ centers.T, -1, 1)
        np.fill_diagonal(pairwise, -np.inf)
        strong_rows = np.flatnonzero(formal_labels >= 0)
        final_scores = np.clip(x[strong_rows] @ centers.T, -1, 1)
        positions = {group["raw_label"]: i for i, group in enumerate(formal)}
        for group in formal:
            position = positions[group["raw_label"]]
            nearest = int(pairwise[position].argmax()) if len(formal) > 1 else None
            group["nearest_center"] = formal[nearest]["id"] if nearest is not None else None
            group["nearest_center_similarity"] = float(pairwise[position, nearest]) if nearest is not None else None
            if nearest is not None and pairwise[position, nearest] >= .85:
                group["flags"].append("CENTER_OVERLAP_REVIEW")
            selected = formal_labels[strong_rows] == group["raw_label"]
            assigned = final_scores[selected, position]
            alternatives = np.delete(final_scores[selected], position, axis=1)
            final_margin = assigned - alternatives.max(axis=1) if len(formal) > 1 else np.full(len(assigned), 2.)
            group["post_trim_below_probe_threshold_count"] = int(((assigned < MIN_COSINE) | (final_margin < MIN_MARGIN)).sum())
            if group["post_trim_below_probe_threshold_count"]:
                group["flags"].append("POST_TRIM_GATE_DRIFT_REVIEW")
    members = [{"row": i, "key": keys[i], "raw_label": int(labels[i]), "formal_label": int(formal_labels[i]),
        "has_abstract": bool(has_abstract[i]), "work_identity": work_ids[i],
        "probe_cosine": float(similarities[i]) if np.isfinite(similarities[i]) else None,
        "probe_margin": float(margins[i]) if np.isfinite(margins[i]) else None,
        "probe_strong": bool(probe_strong[i]), "formal_strong": bool(formal_labels[i] >= 0),
        "weak_noise_reasons": reasons[i], "title_probe": document_title_probe(titles[i])} for i in range(n)]
    strong_count = int((formal_labels >= 0).sum())
    return {"policy": {"version": POLICY_VERSION, "experimental": True,
        "minimum_cosine": MIN_COSINE, "minimum_margin": MIN_MARGIN,
        "minimum_distinct_strong_works": MIN_DISTINCT_STRONG_WORKS,
        "abstract_required": True, "trim_passes": 1, "reassignment": False,
        "eligibility_policy_version": "abstract-required-v2",
        "maximum_items": MAX_ITEMS, "maximum_raw_clusters": MAX_RAW_CLUSTERS},
        "summary": {"library_size": n, "raw_cluster_count": len(groups), "formal_center_count": len(formal),
            "abstract_eligible_count": int(has_abstract.sum()), "title_only_excluded_count": int((~has_abstract).sum()),
            "probe_strong_count": int(probe_strong.sum()), "strong_count": strong_count,
            "strong_coverage": strong_count / n if n else 0.,
            "eligible_strong_coverage": strong_count / int(has_abstract.sum()) if has_abstract.any() else 0.,
            "weak_noise_count": n - strong_count, "weak_noise_fraction": (n - strong_count) / n if n else 0.,
            "weak_noise_reason_counts": {reason: sum(reason in values for values in reasons) for reason in sorted({reason for values in reasons for reason in values})}},
        "raw_labels": labels.tolist(), "formal_labels": formal_labels.tolist(), "clusters": groups, "members": members}
