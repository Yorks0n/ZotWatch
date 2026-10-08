from copy import deepcopy
import json

import numpy as np
import pytest

from zotwatch.interests import center_recall
from zotwatch.interests.center_recall import CenterRecallIndex
from zotwatch.interests.center_recall_contract import (
    CenterRecallModel, EncoderIdentity, FormalCentroid, digest, load_center_model, save_center_model,
)


def unit(rows):
    x = np.zeros((len(rows),384))
    if rows: x[:,:len(rows[0])] = rows
    return x / np.linalg.norm(x,axis=1,keepdims=True) if rows else x


def model(rows=((1.,0.),(0.,1.)), threshold=.55):
    encoder=EncoderIdentity(model_identifier="sentence-transformers/all-MiniLM-L6-v2",
        model_revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41", dimension=384,
        text_policy="title-abstract-token-windows-128-roundtrip-safe-mean-v2",
        artifact_sha256={"model.safetensors":"b"*64})
    return CenterRecallModel(interest_model_revision="a"*32, source_snapshot_sha256="c"*64,
        encoder=encoder, embedding_text_fingerprint=digest(encoder.model_dump()), threshold=threshold,
        centers=[FormalCentroid(interest_id=f"00000000-0000-4000-8000-{i+1:012}",centroid=row.tolist()) for i,row in enumerate(unit(rows))])


def recall(m,x,ids=None):
    return CenterRecallIndex(m).recall(ids or [f"new-{i}" for i in range(len(x))],x,
        embedding_text_fingerprint=m.embedding_text_fingerprint)


def test_recall_depends_only_on_centroid_threshold_with_minimal_evidence():
    m=model(rows=((1.,0.),),threshold=.8)
    results=recall(m,unit([[.8,.6],[.79,(1-.79**2)**.5],[1,0]]))
    assert [row['recalled'] for row in results]==[True,False,True]
    assert results[0]['best_match']['center_cosine']==pytest.approx(.8)
    assert set(results[0])=={'candidate_id','recalled','best_match','secondary_matches',
        'interest_model_revision','embedding_text_fingerprint','recall_policy','threshold'}
    assert set(results[0]['best_match'])=={'interest_id','center_cosine'}
    assert results[0]['interest_model_revision']=='a'*32
    assert results[0]['recall_policy']=='center-recall-v1'
    assert results[0]['threshold']==.8


def test_multiple_matches_are_ordered_with_deterministic_ties_not_accumulated():
    m=model();x=unit([[1,1]])
    result=recall(m,x)[0]
    assert result['best_match']['interest_id']==m.centers[0].interest_id
    assert len(result['secondary_matches'])==1
    assert result['best_match']['center_cosine']==pytest.approx(2**-.5)
    assert result==recall(m,x)[0]
    assert not recall(model(threshold=.8),x)[0]['recalled']


def test_only_three_secondary_centers_are_retained():
    result=recall(model(rows=[[1,0]]*6),unit([[1,0]]))[0]
    assert len(result['secondary_matches'])==3
    assert result['best_match']['interest_id'].endswith('000000000001')


def test_runtime_model_can_load_alone_without_any_library_file(tmp_path,monkeypatch):
    m=model();path=tmp_path/'model.json';save_center_model(path,m)
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('runtime must not load member vectors'))
    loaded=load_center_model(path)
    assert recall(loaded,unit([[1,0]]))[0]['recalled']
    assert list(tmp_path.iterdir())==[path]
    with pytest.raises(FileExistsError):save_center_model(path,m)


def test_service_loads_only_projection_and_returns_fingerprint(tmp_path,monkeypatch):
    m=model();path=tmp_path/'model.json';save_center_model(path,m)
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('runtime must not load member vectors'))
    def fake_encode(loaded,cache,records):
        assert loaded==m and cache=='encoder-cache'
        return unit([[1,0]]),{'embedding_text_fingerprint':m.embedding_text_fingerprint,'record_count':1}
    monkeypatch.setattr(center_recall,'encode_candidates',fake_encode)
    output=center_recall.recall_candidates(path,'encoder-cache',[{'id':'paper','title':'title','abstract':'abstract'}])
    assert output['results'][0]['embedding_text_fingerprint']==m.embedding_text_fingerprint
    assert output['results'][0]['recalled']


def test_candidate_fingerprint_mismatch_rejected():
    with pytest.raises(ValueError,match='fingerprint'):
        CenterRecallIndex(model()).recall(['new'],unit([[1,0]]),embedding_text_fingerprint='f'*64)


@pytest.mark.parametrize('values',[np.zeros((1,384)),np.full((1,384),np.nan),np.ones((1,3))])
def test_invalid_candidate_vectors_rejected(values):
    with pytest.raises(ValueError):recall(model(),values)


def test_invalid_ids_alignment_budget_and_empty_recall():
    m=model();index=CenterRecallIndex(m)
    for ids,values in [(['same','same'],unit([[1,0],[1,0]])),([''],unit([[1,0]])),(['a']*2001,np.empty((2001,384))),(['a','b'],unit([[1,0]]))]:
        with pytest.raises(ValueError):index.recall(ids,values,embedding_text_fingerprint=m.embedding_text_fingerprint)
    assert index.recall([],np.empty((0,384)),embedding_text_fingerprint=m.embedding_text_fingerprint)==[]
    assert not recall(model(rows=()),unit([[1,0]]))[0]['recalled']


@pytest.mark.parametrize('extra',['strong_works','support_count','member_threshold','minimum_distinct_support','representatives'])
def test_runtime_contract_forbids_member_gate_or_support_payload(extra):
    raw=model().model_dump();raw[extra]=[]
    with pytest.raises(ValueError):CenterRecallModel.model_validate(raw)


def test_closed_identity_revision_unit_and_checksum_contract(tmp_path):
    raw=model().model_dump()
    for change in ('fingerprint','revision','policy','centroid','threshold','ids','encoder'):
        invalid=deepcopy(raw)
        if change=='fingerprint':invalid['embedding_text_fingerprint']='d'*64
        if change=='revision':invalid['interest_model_revision']='bad'
        if change=='policy':invalid['recall_policy']='center-supported-recall-experimental-v1'
        if change=='centroid':invalid['centers'][0]['centroid']=[0.]*384
        if change=='threshold':invalid['threshold']=float('nan')
        if change=='ids':invalid['centers'][1]['interest_id']=invalid['centers'][0]['interest_id']
        if change=='encoder':invalid['encoder']['model_revision']='unpinned'
        with pytest.raises(ValueError):CenterRecallModel.model_validate(invalid)
    path=tmp_path/'model.json';save_center_model(path,model())
    envelope=json.loads(path.read_text());envelope['model']['threshold']=.4;path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError,match='checksum'):load_center_model(path)


def test_title_only_candidate_rejected_before_encoder_loading():
    with pytest.raises(ValueError,match='abstract required'):
        center_recall.encode_candidates(model(),'no-cache',[{'id':'x','title':'title','abstract':None}])
    assert center_recall.encode_candidates(model(),'no-cache',[])[0].shape==(0,384)
