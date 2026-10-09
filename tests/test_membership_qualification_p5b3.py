import numpy as np
import pytest

from zotwatch.interests.membership_qualification import (
    document_title_probe, qualify_membership, work_identities,
)
from zotwatch.interests.interest_formation import form_interest_model


def run(x, labels, abstracts=None, titles=None, dois=None):
    n = len(x)
    return qualify_membership(np.asarray(x), np.asarray(labels, dtype=int), [f"K{i:03}" for i in range(n)],
        titles or [f"distinct work {i}" for i in range(n)], abstracts or ["abstract"] * n, dois=dois)


def test_qualification_rejects_title_only_raw_membership():
    base = np.vstack([np.tile([1., 0., 0.], (12, 1)), np.tile([0., 1., 0.], (12, 1))])
    extended = np.vstack([base, np.tile([0., 0., 1.], (30, 1))])
    labels = np.array([7] * 12 + [9] * 12 + [7] * 30)
    saved = labels.copy()
    with pytest.raises(ValueError, match="filter before auto_spherical"):
        run(extended, labels, ["abstract"] * 24 + ["  "] * 30)
    assert np.array_equal(labels, saved)


def test_ambiguous_high_cosine_and_weak_members_are_not_reassigned():
    left, right = np.array([1., .25, 0.]), np.array([1., -.25, 0.])
    x = np.vstack([np.tile(left, (12, 1)), np.tile(right, (12, 1)), [1., 0., 0.], [0., 0., 1.]])
    result = run(x, [0] * 12 + [1] * 12 + [0, 0])
    ambiguous, weak = result["members"][-2:]
    assert ambiguous["probe_cosine"] > .9 and ambiguous["probe_margin"] < .02
    assert "ambiguous_margin" in ambiguous["weak_noise_reasons"]
    assert "weak_cosine" in weak["weak_noise_reasons"]
    assert result["formal_labels"][-2:] == [-1, -1]
    assert result["summary"]["formal_center_count"] == 2
    assert result["clusters"][0]["strong_support"] == 12
    trimmed = np.array(result["clusters"][0]["trimmed_centroid"])
    assert np.allclose(trimmed, left / np.linalg.norm(left))
    # Frozen gate, independent of any later centroid: no second qualification.
    for member in result["members"]:
        expected = member["probe_cosine"] >= .5 and member["probe_margin"] >= .02
        assert member["probe_strong"] == expected


def test_ten_distinct_strong_works_is_a_center_gate_not_raw_size():
    x = np.tile([1., 0.], (20, 1))
    titles = [f"work {i % 9}" for i in range(20)]
    rejected = run(x, [4] * 20, titles=titles)
    assert rejected["summary"]["probe_strong_count"] == 20
    assert rejected["summary"]["strong_count"] == 0
    assert rejected["summary"]["formal_center_count"] == 0
    assert rejected["clusters"][0]["distinct_strong_works"] == 9
    assert all(member["weak_noise_reasons"] == ["unpublished_raw_cluster"] for member in rejected["members"])
    titles[-1] = "a tenth distinct work"
    accepted = run(x, [4] * 20, titles=titles)
    assert accepted["summary"]["formal_center_count"] == 1
    assert accepted["clusters"][0]["distinct_strong_works"] == 10
    reps = accepted["clusters"][0]["representatives"]
    assert len({rep["title"] for rep in reps}) == 5
    assert set(accepted["formal_labels"]) == {4}


def test_all_title_only_is_ineligible_without_fitting_or_weak_noise():
    x = np.vstack([np.tile([1., 0.], (10, 1)), np.tile([1., 0.], (15, 1))])
    result = form_interest_model(x, [str(i) for i in range(25)], ["title"] * 25, [None] * 25)
    assert result["summary"]["formal_center_count"] == 0
    assert result["summary"]["weak_noise_count"] == 0
    assert result["summary"]["ineligible_count"] == 25
    assert result["selector"]["fit_count"] == 0
    assert result["qualification"]["raw_labels"] == []
    assert all(row["raw_label"] is None and row["formal_label"] is None for row in result["ineligible_records"])


def test_raw_noise_empty_input_and_cancelled_centroid_are_explicit():
    result = run(np.tile([1., 0.], (10, 1)), [-1] * 10)
    assert result["summary"]["raw_cluster_count"] == result["summary"]["formal_center_count"] == 0
    assert result["summary"]["weak_noise_fraction"] == 1
    empty = run(np.empty((0, 2)), [])
    # Empty labels need an integer dtype, as supplied by actual raw fitter.
    assert empty["summary"]["library_size"] == 0
    cancelled = run(np.vstack([np.tile([1., 0.], (5, 1)), np.tile([-1., 0.], (5, 1))]), [0] * 10)
    assert cancelled["clusters"][0]["probe_centroid"] is None
    assert all(m["weak_noise_reasons"] == ["undefined_probe_centroid"] for m in cancelled["members"])


def test_exact_work_union_and_tiny_title_probe_do_not_modify_source():
    identities = work_identities(["A", "B", "C", "D"], ["Paper", " paper ", "alternate", "unique"],
        ["a"] * 4, [None, "https://doi.org/10.1/X", "DOI:10.1/x", None])
    assert identities[0] == identities[1] == identities[2] != identities[3]
    assert document_title_probe("PDF: Scientific title.pdf")["suggested_title"] == "Scientific title"
    assert document_title_probe("Plant ion transport [PDF]")["flag"]
    assert document_title_probe("The RAxML v8.2.X Manual")["suggested_title"] == "The RAxML v8.2.X"
    assert document_title_probe("Salt tolerance and full text mining")["flag"] is None
    assert document_title_probe("盐生植物和离子转运")["flag"] is None


def test_title_only_metadata_cannot_bridge_distinct_strong_works():
    titles = [f"work {i}" for i in range(10)] + ["work 0"]
    dois = [None, "10.1/b"] + [None] * 8 + ["10.1/b"]
    pytest.importorskip("sklearn")
    result = form_interest_model(np.tile([1., 0.], (11, 1)), [str(i) for i in range(11)],
        titles, ["abstract"] * 10 + [None], dois=dois)
    assert result["qualification"]["clusters"][0]["distinct_strong_works"] == 10
    assert result["summary"]["formal_center_count"] == 1


def test_ineligible_rows_cannot_affect_k_labels_centroids_or_coverage():
    pytest.importorskip("sklearn")
    x = np.repeat(np.eye(4), 30, axis=0)
    keys, titles = [str(i) for i in range(120)], [f"work {i}" for i in range(120)]
    base = form_interest_model(x, keys, titles, ["abstract"] * 120)
    expanded = form_interest_model(np.vstack([x, np.full((331, 4), np.nan)]),
        keys + [f"excluded{i}" for i in range(331)], titles + ["title-only"] * 331,
        ["abstract"] * 120 + [None] * 331)
    assert base["qualification"] == expanded["qualification"]
    assert base["selector"] == expanded["selector"]
    assert expanded["summary"]["eligible_count"] == expanded["summary"]["strong_count"] == 120
    assert expanded["summary"]["weak_noise_count"] == 0
    assert expanded["summary"]["strong_coverage"] == 1
    assert len(expanded["ineligible_records"]) == 331


def test_center_budget_rejects_unbounded_partition_before_pairwise_scores():
    with pytest.raises(ValueError, match="60-center"):
        run(np.ones((61, 2)), list(range(61)))


def test_file_wrapper_diagnostic_never_changes_membership_or_vectors():
    x = np.tile([1., 0.], (10, 1))
    clean = run(x, [0] * 10, titles=[f"work {i}" for i in range(10)])
    wrapped = run(x, [0] * 10, titles=[f"work {i}.pdf" for i in range(10)])
    assert clean["formal_labels"] == wrapped["formal_labels"]
    assert clean["clusters"][0]["trimmed_centroid"] == wrapped["clusters"][0]["trimmed_centroid"]
    assert "DOCUMENT_TITLE_WRAPPER_REVIEW" in wrapped["clusters"][0]["flags"]


def test_post_trim_gate_drift_is_flagged_without_a_second_qualification_pass():
    rng = np.random.default_rng(0)
    angles = np.concatenate([rng.normal(center, .12, 30) for center in (0., .4, .8)])
    result = run(np.column_stack([np.cos(angles), np.sin(angles)]), np.repeat(np.arange(3), 30))
    assert result["summary"]["formal_center_count"] == 3
    assert result["summary"]["probe_strong_count"] == result["summary"]["strong_count"] == 79
    assert sum(g["post_trim_below_probe_threshold_count"] for g in result["clusters"]) == 1
    for member in result["members"]:
        assert member["formal_strong"] == member["probe_strong"]
        assert member["formal_label"] in (-1, member["raw_label"])


@pytest.mark.parametrize("change", ["shape", "nan", "zero", "labels", "alignment", "duplicate_keys"])
def test_bad_inputs_fail_instead_of_silently_filtering(change):
    x, labels = np.ones((10, 2)), np.zeros(10, dtype=int)
    keys, titles, abstracts = [str(i) for i in range(10)], ["title"] * 10, ["abstract"] * 10
    if change == "shape": x = np.ones(10)
    if change == "nan": x[0, 0] = np.nan
    if change == "zero": x[0] = 0
    if change == "labels": labels = labels.astype(float)
    if change == "alignment": abstracts.pop()
    if change == "duplicate_keys": keys[-1] = keys[0]
    with pytest.raises(ValueError):
        qualify_membership(x, labels, keys, titles, abstracts)
