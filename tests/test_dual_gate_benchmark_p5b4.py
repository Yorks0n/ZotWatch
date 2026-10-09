from copy import deepcopy

import numpy as np
import pytest

from scripts.benchmarks.dual_gate_recall import CenterRecallIndex, RecallParameters, recall_candidates
from zotwatch.interests.frozen_candidate import POLICY, semantic_text
from zotwatch.interests.membership_qualification import qualify_membership


def unit(rows):
    x = np.zeros((len(rows), 384))
    x[:, :len(rows[0])] = rows
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def snapshot(vectors, titles=None, labels=None):
    n = len(vectors)
    keys = [f"K{i:03}" for i in range(n)]
    q = qualify_membership(vectors, np.zeros(n, dtype=int) if labels is None else np.array(labels), keys,
        titles or [f"work {i}" for i in range(n)], ["abstract"] * n)
    centers = []
    for group in q["clusters"]:
        if not group["published"]:
            continue
        works = [{"key": keys[row], "input_row": row, "work_identity": q["members"][row]["work_identity"]}
            for row in group["probe_strong_rows"]]
        centers.append({"interest_id": f"stable-{group['id']}", "model_revision": "a" * 32,
            "raw_cluster_alias": group["id"], "centroid": group["trimmed_centroid"],
            "member_keys": sorted(w["key"] for w in works), "strong_works": works,
            "distinct_strong_works": group["distinct_strong_works"]})
    return {"model_revision": "a" * 32, "policy": deepcopy(POLICY), "centers": centers,
        "formation": {"eligible_input_rows": list(range(n)), "eligible_keys": keys, "qualification": q}}


def test_one_very_similar_work_cannot_qualify_even_with_centroid_gate():
    # All qualify as old library members, but only one supports a NEW paper at .95.
    vectors = unit([[1,0]] + [[.6,.8]] * 10 + [[.6,-.8]])
    state = snapshot(vectors)
    result = CenterRecallIndex(state, vectors).recall(["new"], unit([[1,0]]), RecallParameters(.1,.95,2))[0]
    assert len(result["possible_centers"]) == 1
    assert result["possible_centers"][0]["support_count"] == 1
    assert not result["recalled"] and result["best_match"] is None


def test_duplicate_records_count_once_and_log_best_variant():
    vectors = unit([[1,0]] * 12 + [[.6,.8]] * 10)
    state = snapshot(vectors, titles=["same work"] * 12 + [f"other {i}" for i in range(10)])
    result = CenterRecallIndex(state, vectors).recall(["new"], unit([[1,0]]), RecallParameters(.1,.95,2))[0]
    assert result["possible_centers"][0]["support_count"] == 1
    assert len(result["possible_centers"][0]["top_supporting_works"]) == 1
    assert not result["recalled"]


def test_same_center_support_only_no_cross_center_accumulation():
    x = unit([[1,0]] * 12 + [[0,1]] * 12)
    state = snapshot(x, labels=[0]*12+[1]*12)
    query = unit([[1,1]])
    index = CenterRecallIndex(state,x)
    result = index.recall(["new"],query,RecallParameters(.7,.7,3))[0]
    assert result["recalled"] and result["matched_center_count"] == 2
    assert result["recall_score"] == pytest.approx(2**-.5)
    assert result["best_match"]["support_count"] == 12
    assert len(result["secondary_matches"]) == 1
    assert result["interest_model_revision"] == "a"*32
    assert result == index.recall(["new"],query,RecallParameters(.7,.7,3))[0]
    assert not index.recall(["new"],query,RecallParameters(.7,.7,13))[0]["recalled"]


def test_member_gate_cannot_bypass_centroid_gate():
    x = unit([[1,0]] * 12)
    result = CenterRecallIndex(snapshot(x),x).recall(["new"],unit([[.8,.6]]),RecallParameters(.9,.7,2))[0]
    assert result["possible_centers"] == [] and not result["recalled"]


def test_evidence_is_thresholded_distinct_and_retains_exact_revision():
    x = unit([[1,0]] * 12)
    result = CenterRecallIndex(snapshot(x),x).recall(["new"],unit([[1,0]]),RecallParameters(1.,1.,2))[0]
    assert result["best_match"]["support_count"] == 12
    assert len(result["best_match"]["top_supporting_works"]) == 5
    assert len({w["work_identity"] for w in result["best_match"]["top_supporting_works"]}) == 5
    assert result["best_match"]["interest_id"] == "stable-C01"
    assert result["recall_score"] == 1.


def test_ineligible_weak_or_wrong_center_cannot_supply_support():
    x = unit([[1,0]]*12 + [[0,1]]*12)
    state = snapshot(x, labels=[0]*12+[1]*12)
    invalid = deepcopy(state)
    invalid["formation"]["eligible_input_rows"].remove(0)
    with pytest.raises(ValueError,match="Ineligible|misaligned"):
        CenterRecallIndex(invalid,x)
    invalid = deepcopy(state)
    invalid["formation"]["qualification"]["members"][0]["formal_strong"] = False
    with pytest.raises(ValueError,match="qualified strong"):
        CenterRecallIndex(invalid,x)
    invalid = deepcopy(state)
    invalid["formation"]["qualification"]["members"][0]["formal_label"] = 1
    with pytest.raises(ValueError,match="qualified strong"):
        CenterRecallIndex(invalid,x)


@pytest.mark.parametrize("parameters",[(.5,.5,1),(.5,.5,True),(float('nan'),.5,2),(.5,1.1,2)])
def test_parameters_are_explicit_and_bounded(parameters):
    with pytest.raises(ValueError): RecallParameters(*parameters)


def test_invalid_inputs_and_policy_drift_rejected():
    x = unit([[1,0]]*12); state = snapshot(x); index=CenterRecallIndex(state,x)
    for ids, values in [(["a","a"],unit([[1,0],[1,0]])),(["a"],np.zeros((1,384))),(["a"],np.ones((1,3)))]:
        with pytest.raises(ValueError): index.recall(ids,values,RecallParameters(.6,.6,2))
    state["policy"]["minimum_cosine"] = .4
    with pytest.raises(ValueError,match="policy"): CenterRecallIndex(state,x)


def test_no_centers_and_no_candidates_are_valid_empty_recall():
    x = unit([[1,0]]*9); index=CenterRecallIndex(snapshot(x),x)
    assert not index.recall(["a"],unit([[1,0]]),RecallParameters(.6,.6,2))[0]["recalled"]
    assert index.recall([],np.empty((0,384)),RecallParameters(.6,.6,2)) == []


def test_title_only_semantic_input_has_no_fallback():
    assert semantic_text(" title "," abstract ") == "title\nabstract"
    for abstract in (None,"", "  "):
        with pytest.raises(ValueError,match="insufficient_semantic_text"): semantic_text("title",abstract)


def test_service_checks_budget_and_ids_before_any_embedding():
    with pytest.raises(ValueError,match="budget"):
        recall_candidates("unused","unused",[{}]*2001,RecallParameters(.6,.6,2))
    with pytest.raises(ValueError,match="unique"):
        recall_candidates("unused","unused",[{"id":"same"},{"id":"same"}],RecallParameters(.6,.6,2))
