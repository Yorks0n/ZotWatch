import numpy as np
import pytest

pytest.importorskip("sklearn", reason="P5B3 optional clustering runtime required")

from zotwatch.interests.clustering_benchmark import (
    canonical_labels, core_transitions, evaluate_partition, spherical_kmeans,
)


def directions():
    rng = np.random.default_rng(4)
    groups = []
    for center in np.eye(8)[:4]:
        group = center + rng.normal(0, .01, (15, 8))
        groups.extend(group / np.linalg.norm(group, axis=1, keepdims=True))
    return np.asarray(groups)


def test_spherical_updates_cosine_optimum_and_reproducibility():
    x = directions()
    labels, info = spherical_kmeans(x, 4, restarts=3)
    centers = np.asarray([x[labels == label].mean(axis=0) for label in range(4)])
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    assert sorted(np.bincount(labels)) == [15] * 4
    assert np.array_equal(np.argmax(x @ centers.T, axis=1), labels)
    assert info["objective"] == pytest.approx(float(np.sum(x * centers[labels])), abs=1e-12)
    assert info["converged"]
    again, repeated = spherical_kmeans(x, 4, restarts=3)
    assert np.array_equal(again, labels) and repeated == info


def test_raw_coverage_does_not_hide_weak_members_and_singletons():
    # Three strong 10-member interests, one assigned unrelated member, one singleton.
    x = np.concatenate([np.tile(np.eye(4)[i], (10, 1)) for i in range(3)] + [np.array([[0., 0., 0., 1.]])] * 2)
    labels = np.array([0] * 10 + [1] * 10 + [2] * 10 + [0, 3])
    result, canonical = evaluate_partition(x, labels, [f"K{i:02}" for i in range(32)], ["synthetic"] * 32)
    assert result["summary"]["coverage"] == 1.0
    assert result["summary"]["supported_items"] == 30
    assert result["summary"]["minimum_cluster_size"] == 1
    assert "SMALL_SUPPORT" in result["clusters"][3]["flags"]
    assert result["clusters"][0]["weak_count"] == 1
    assert result["clusters"][0]["non_nearest_assignment_count"] == 1
    assert sum(c["size"] for c in result["clusters"]) == 32


def test_noise_metrics_representatives_and_label_numbering():
    x = directions()
    labels = np.array([7] * 15 + [9] * 15 + [3] * 15 + [-1] * 15)
    keys = [f"K{i:02}" for i in range(len(x))]
    result, canonical = evaluate_partition(x, labels, keys, [f"paper {i}" for i in range(len(x))])
    assert result["summary"]["cluster_count"] == 3
    assert result["summary"]["coverage"] == .75
    assert result["summary"]["outlier_percent"] == .25
    assert result["summary"]["within_cosine_mean"] > .99
    assert result["summary"]["nearest_center_max"] < .05
    for cluster in result["clusters"]:
        representatives = cluster["representatives"]
        assert len(representatives) == 5 and len({p["row"] for p in representatives}) == 5
        assert [p["cosine"] for p in representatives] == sorted([p["cosine"] for p in representatives], reverse=True)
        assert all(canonical[p["row"]] == int(cluster["id"][1:]) - 1 for p in representatives)
    assert np.array_equal(canonical, canonical_labels(labels, keys))


def test_core_preservation_is_distinct_from_separation():
    core = np.array([0, 0, 0, 1, 1, 1, -1])
    # Both topics retained completely in the same giant center: no separation.
    merged = core_transitions(core, np.zeros(7, dtype=int))
    assert all(c["dominant_share"] == 1 for c in merged)
    assert merged[0]["dominant_center"] == merged[1]["dominant_center"]
    split = core_transitions(core, np.array([0, 0, 1, 2, 2, 2, -1]))
    assert split[0]["dominant_share"] == pytest.approx(2 / 3)
    assert split[1]["dominant_share"] == 1


def test_k_above_unique_directions_is_rejected():
    with pytest.raises(ValueError, match="distinct"):
        spherical_kmeans(np.tile([1., 0.], (10, 1)), 2)
