import numpy as np
import pytest

from zotwatch.interests.auto_spherical import auto_spherical, select_spherical_k


def test_size_schedule_is_bounded_monotone_and_coarse_for_large_libraries():
    examples = {0: 0, 9: 0, 10: 1, 20: 1, 50: 2, 100: 5, 200: 10,
        300: 14, 500: 18, 1000: 25, 1527: 30, 2000: 35, 3000: 45,
        5000: 55, 10000: 60, 100000: 60}
    assert {n: select_spherical_k(n)["selected_k"] for n in examples} == examples
    previous = 0
    for n in range(10001):
        result = select_spherical_k(n)
        k = result["selected_k"]
        assert previous <= k <= min(n, 60)
        if k > 1:
            assert n / k >= 20
        previous = k
    assert {select_spherical_k(n)["selected_k"] for n in range(1450, 1601)} == {30}
    assert select_spherical_k(10000)["maximum_centers_reached"]


@pytest.mark.parametrize("n,d", [(-1, None), (True, None), (10.5, None),
    (10, 11), (10, -1), (10, 0), (0, 1), (10, True), (10, 1.5)])
def test_invalid_size_or_direction_count_rejected(n, d):
    with pytest.raises(ValueError):
        select_spherical_k(n, distinct_directions=d)


@pytest.mark.parametrize("x", [np.ones(5), np.empty((10, 0)), np.zeros((10, 2)),
    np.array([[np.nan, 1.]]), np.array([[np.inf, 1.]])])
def test_invalid_embeddings_fail_before_fit(x):
    with pytest.raises(ValueError):
        auto_spherical(x)


@pytest.mark.parametrize("kwargs", [{"restarts": 0}, {"max_iter": 0}, {"seed": -1},
    {"restarts": 1.5}, {"max_iter": True}, {"seed": 1.5}])
def test_invalid_budget_rejected_even_for_empty_library(kwargs):
    with pytest.raises(ValueError):
        auto_spherical(np.empty((0, 8)), **kwargs)


def test_small_and_empty_libraries_never_fit():
    for n in (0, 1, 9):
        labels, info = auto_spherical(np.ones((n, 8)))
        assert labels.shape == (n,) and np.all(labels == -1)
        assert info["fit_count"] == 0 and info["fit"] is None
        assert info["selection"]["reason"] == "insufficient_library"


def test_duplicate_direction_cap_and_single_center_are_valid():
    pytest.importorskip("sklearn")
    x = np.tile([1., 0., 0.], (100, 1))
    labels, info = auto_spherical(x)
    assert info["selection"]["selected_k"] == 1
    assert info["selection"]["distinct_directions"] == 1
    assert np.all(labels == 0) and info["fit_count"] == 1
    assert info["fit"]["converged"]


def test_wrapper_calls_unchanged_fitter_once_and_matches_explicit_k(monkeypatch):
    pytest.importorskip("sklearn")
    from zotwatch.interests import clustering_benchmark as benchmark
    rng = np.random.default_rng(70)
    x = np.concatenate([center + rng.normal(0, .01, (30, 8)) for center in np.eye(8)[:4]])
    original = x.copy()
    fitter = benchmark.spherical_kmeans
    calls = []
    def counted(vectors, k, **kwargs):
        calls.append((k, kwargs))
        return fitter(vectors, k, **kwargs)
    monkeypatch.setattr(benchmark, "spherical_kmeans", counted)
    labels, info = auto_spherical(x, seed=12, restarts=3, max_iter=100)
    assert calls == [(6, {"seed": 12, "restarts": 3, "max_iter": 100})]
    direct, fit = fitter(x, 6, seed=12, restarts=3, max_iter=100)
    assert np.array_equal(labels, direct) and info["fit"] == fit
    assert np.array_equal(x, original)
