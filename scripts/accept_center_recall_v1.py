"""Bounded centroid-only quality acceptance, using preserved benchmark vectors."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zotwatch.interests.center_recall import CenterRecallIndex
from zotwatch.interests.center_recall_contract import load_center_model
from zotwatch.interests.frozen_candidate import load_frozen, digest, file_digest
from evaluate_center_recall import metrics


def accept(model_path, capsule, evaluation, output):
    output=Path(output)
    if output.exists():raise ValueError("Acceptance evidence cannot overwrite a previous result")
    model=load_center_model(model_path)
    snapshot, _, _=load_frozen(capsule)  # offline acceptance only; never recommendation runtime
    evaluation=Path(evaluation)
    original=json.loads((evaluation/'comparison.json').read_text())
    vectors=np.load(evaluation/'candidate_embeddings.npy',allow_pickle=False)
    assert file_digest(evaluation/'candidate_embeddings.npy')==original['candidate_vector_sha256']
    assert model.interest_model_revision==snapshot['model_revision']==original['interest_model_revision']
    assert model.source_snapshot_sha256==digest(snapshot)==original['exact_snapshot_sha256']
    assert model.threshold==original['selected']['centroid']['preset']==.55
    expected={center['interest_id']:center['centroid'] for center in snapshot['centers']}
    assert {center.interest_id:center.centroid for center in model.centers}==expected
    assert model.encoder.artifact_sha256==snapshot['encoder_artifacts']
    records=[row['candidate'] for row in original['records']]
    with threadpool_limits(limits=1):
        index=CenterRecallIndex(model)
        results=index.recall([r['id'] for r in records],vectors,embedding_text_fingerprint=model.embedding_text_fingerprint)
        assert results==index.recall([r['id'] for r in records],vectors,embedding_text_fingerprint=model.embedding_text_fingerprint)
    for actual, old in zip(results,original['records']):
        assert actual['recalled']==old['centroid']['recalled']
        if actual['recalled']:
            assert actual['best_match']['interest_id']==old['centroid']['interest_id']
            assert actual['best_match']['center_cosine']==old['centroid']['cosine']
        assert set(actual)=={'candidate_id','recalled','best_match','secondary_matches','interest_model_revision',
            'embedding_text_fingerprint','recall_policy','threshold'}
    decisions=[row['recalled'] for row in results]
    met={split:metrics(records,decisions,split) for split in ('calibration','heldout')}
    assert met==original['metrics']['centroid']
    payload={'status':'PASS','scope':'bounded-existing-real-library-and-public-candidate-cohort',
        'recall_policy':model.recall_policy,'threshold':model.threshold,
        'interest_model_revision':model.interest_model_revision,'source_snapshot_sha256':model.source_snapshot_sha256,
        'embedding_text_fingerprint':model.embedding_text_fingerprint,'model_file_sha256':file_digest(model_path),
        'candidate_vectors_sha256':original['candidate_vector_sha256'],'cohort_sha256':original['cohort_sha256'],
        'historical_comparison_sha256':file_digest(evaluation/'comparison.json'),'candidate_count':len(records),
        'formal_centroid_count':len(model.centers),'runtime_model_bytes':Path(model_path).stat().st_size,
        'centroid_matrix_bytes':index.centroids.nbytes,
        'deterministic':True,'exact_centroid_decisions_and_scores_match_history':True,
        'formal_centroids_stable_ids_and_revision_unchanged':True,'member_vectors_in_runtime':False,
        'metrics':met,'results':results,'llm_calls':0,'rollout':False}
    output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({key:value for key,value in payload.items() if key!='results'},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('model','capsule','evaluation','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    accept(args.model,args.capsule,args.evaluation,args.output)
