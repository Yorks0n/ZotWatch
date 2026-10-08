"""Local, size-based K selector around the unchanged spherical K-means fitter.

This is a center-formation experiment, not a published latent-model backend.
Selection uses no labels, titles, semantic judgments, or additional model fits.
"""
from __future__ import annotations

from math import floor, sqrt
from numbers import Integral

import numpy as np

POLICY_VERSION = "auto-spherical-size-v1"
SIZE_COEFFICIENT = 0.8
MIN_LIBRARY_SIZE = 10
TARGET_MIN_AVERAGE_SUPPORT = 20
MAX_CENTERS = 60
ROUNDING_START = 20
ROUNDING_STEP = 5


def select_spherical_k(library_size, *, distinct_directions=None):
    """Return K and its auditable size prior; caps do not ensure per-center support.

    N counts eligible, nonempty title/abstract embedding rows. A supplied exact
    direction count caps K for duplicate/degenerate inputs. Small libraries below
    ten rows return K=0 rather than manufacture an interest from a few papers.
    """
    if isinstance(library_size, bool) or not isinstance(library_size, Integral) or library_size < 0:
        raise ValueError("Library size must be a nonnegative integer")
    n = int(library_size)
    distinct = n if distinct_directions is None else distinct_directions
    if isinstance(distinct, bool) or not isinstance(distinct, Integral) or not 0 <= distinct <= n:
        raise ValueError("Distinct directions must be an integer between zero and library size")
    distinct = int(distinct)
    if n > 0 and distinct == 0:
        raise ValueError("A nonempty valid vector library must have at least one direction")
    prior = SIZE_COEFFICIENT * sqrt(n)
    # Explicit half-up rounding avoids platform/language bankers-rounding ties.
    rounded = floor(prior / ROUNDING_STEP + 0.5) * ROUNDING_STEP if prior >= ROUNDING_START else floor(prior + 0.5)
    support_cap = max(1, n // TARGET_MIN_AVERAGE_SUPPORT) if n else 0
    k = min(max(1, rounded), support_cap, MAX_CENTERS, distinct) if n >= MIN_LIBRARY_SIZE else 0
    return {"strategy": "auto_spherical", "policy_version": POLICY_VERSION,
        "library_size": n, "distinct_directions": distinct, "size_prior": prior,
        "rounded_prior": rounded, "support_cap": support_cap, "maximum_centers": MAX_CENTERS,
        "selected_k": k, "reason": "insufficient_library" if not k else "size_prior_with_caps",
        "maximum_centers_reached": bool(k and k == MAX_CENTERS),
        "policy": {"size_coefficient": SIZE_COEFFICIENT, "minimum_library_size": MIN_LIBRARY_SIZE,
            "target_min_average_support": TARGET_MIN_AVERAGE_SUPPORT,
            "rounding_start": ROUNDING_START, "rounding_step": ROUNDING_STEP}}


def auto_spherical(vectors, *, seed=20260930, restarts=10, max_iter=300):
    """Select one K, then call the existing fitter once with its original defaults.

    Empty/small libraries return all-noise labels without a fit. Diagnostics and
    later membership decisions are separate; successful fitting is not a quality
    PASS. No persistence, revision updates, embedding, or network access occurs.
    """
    x = np.asarray(vectors, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] == 0:
        raise ValueError("Expected a two-dimensional vector matrix with positive dimension")
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    if not np.isfinite(x).all() or not np.isfinite(norms).all() or np.any(norms <= 1e-12):
        raise ValueError("Invalid clustering vectors")
    for value in (seed, restarts, max_iter):
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise ValueError("Spherical run budget and seed must be integers")
    if seed < 0 or restarts < 1 or max_iter < 1:
        raise ValueError("Invalid spherical K-means run budget")
    distinct = len(np.unique(x / norms, axis=0))
    selection = select_spherical_k(len(x), distinct_directions=distinct)
    info = {"selection": selection, "fit_count": 0, "fit": None}
    if not selection["selected_k"]:
        return np.full(len(x), -1, dtype=np.int64), info
    from .clustering_benchmark import spherical_kmeans
    labels, fit = spherical_kmeans(x, selection["selected_k"], seed=int(seed),
        restarts=int(restarts), max_iter=int(max_iter))
    info.update(fit_count=1, fit=fit)
    return labels, info
