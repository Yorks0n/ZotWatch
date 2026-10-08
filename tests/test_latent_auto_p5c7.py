"""Necessary P5C7 checks: automatic lifecycle, ranking, isolated contract/RSS."""
from datetime import datetime, timezone
from types import SimpleNamespace
import hashlib
import io
import json
import zipfile
import pytest

from src import cli as engine
from zotwatch.interests import auto_runner, lifecycle as lc, recall_integration
from zotwatch.interests.auto_ranking import rank, select
from zotwatch.interests.auto_results import AutoRunResult
from zotwatch.interests.workflow_results import materialize, parse_workflow_result
from .test_latent_recall_integration_p5b5 import pipeline, install_collection, integrated_args
from .test_latent_lifecycle_p5c4 import fake_encode, seed, IDENTITY
from .test_interests_p5b1 import candidate


def test_two_pass_order_identity_and_backfill():
    rows=[dict(work_key=f'a{i}',primary_center_id='a',center_cosine=1-i*.01) for i in range(25)]
    rows += [dict(work_key='b',primary_center_id='b',center_cosine=.6), dict(work_key='c',primary_center_id='c',center_cosine=.6)]
    result=select(rows+rows[:1])
    assert len(result)==20 and len({r['work_key'] for r in result})==20
    assert [r['work_key'] for r in result[:5]]==['a0','a1','a2','b','c']
    assert [r['work_key'] for r in result[5:]]==[f'a{i}' for i in range(3,18)]
    assert select(list(reversed(rows)))==result


def test_rank_uses_best_cosine_without_topic_or_secondary_sum():
    c=candidate('plant',abstract='text',published=datetime.now(timezone.utc))
    c.venue='Plant Physiology'
    decision=dict(candidate_id=f'{c.source}:{c.identifier}',recalled=True,
        best_match={'interest_id':'a','center_cosine':.6},secondary_matches=[{'interest_id':'b','center_cosine':.59}])
    rows=rank([c,c],[decision])
    assert len(rows)==1 and rows[0]['score']==.6 and rows[0]['primary_center_id']=='a'
    assert not any('topic' in k or 'profile' in k for k in rows[0])


@pytest.mark.parametrize('old_profile',['absent','all_muted'])
def test_normal_watch_bootstraps_without_reading_confirmed_profile(pipeline, monkeypatch, tmp_path, old_profile):
    paths,effective,_,_=pipeline
    from .helpers import FixedVectors
    import numpy as np
    monkeypatch.setattr(FixedVectors,'encode',lambda self,texts:np.asarray([[1.,0.,0.]]*len(texts),dtype='float32'))
    monkeypatch.setattr(lc,'validate_encoder',lambda *a:None)
    monkeypatch.setattr(lc,'encode_candidates',fake_encode)
    monkeypatch.setattr(engine.ZoteroIngestor,'run',lambda self,**kw:seed(self.storage))
    import importlib
    monkeypatch.setattr(importlib.import_module('zotwatch.runtime.preflight'),'preflight',lambda *a,**kw:SimpleNamespace(ready=True))
    monkeypatch.setenv('ZOTWATCH_PRIVATE_FILTER_DIAGNOSTIC','1')
    # Library identity matches the fixture's seeded verified mirror.
    monkeypatch.setattr(engine,'_library_identity',lambda _:IDENTITY)
    monkeypatch.setattr(importlib.import_module('zotwatch.interests.runner'),'load_snapshot',lambda *a:pytest.fail('confirmed profile must never be read'))
    if old_profile=='absent':
        import subprocess
        from zotwatch.interests.contract import BRANCH
        subprocess.run(['git','update-ref','-d',f'refs/heads/{BRANCH}'],cwd=paths.workspace,check=True)
    if old_profile=='all_muted':
        from .test_interests_p5b1 import profile,commit_profile
        value=profile();value['library_scope']['id']='123456';value['interests'][0]['status']='muted';commit_profile(paths.workspace,value)
    candidates=[candidate('plant',doi='10.1234/plant',abstract='real semantic text',published=datetime.now(timezone.utc)),
        candidate('old',abstract='text',published=datetime(2020,1,1,tzinfo=timezone.utc))]
    candidates[0].venue="Plant Physiology"
    install_collection(monkeypatch,candidates)
    monkeypatch.setattr(recall_integration,'encode_candidates',fake_encode)
    args=integrated_args(latent_lifecycle='per-user-v1',latent_encoder_cache='unused');args.full=False
    result=auto_runner.run(args,paths,effective)
    assert result.status=='succeeded',result
    assert len(result.recommendations)==1 and result.recommendations[0].work_key=='10.1234/plant'
    assert result.recommendations[0].score==result.recommendations[0].center_cosine
    assert result.evidence.formal_center_ids
    data=result.model_dump_json().encode()
    assert b'confirmed_profile' not in data and b'feedback_commit' not in data and b'matched_interest' not in data
    assert parse_workflow_result(data)==result
    target=tmp_path/'final'
    materialize(paths.state/'runs'/f'latent-auto-{result.run_id}.json',0,paths.state,target)
    assert (target/'latent-auto-result-v1.json').exists()
    assert not list(target.glob('topic*'))
    assert (target/'latent-deployment-evidence-v1.json').exists()
    from zotwatch.workflow.cli import main as workflow_main
    # Exercise the real workflow seal path, not a fabricated legacy envelope.
    assert workflow_main(['seal-result','--private',str(tmp_path), '--engine-sha','a'*40,
        '--repository','owner/private','--repository-id',str(result.evidence.workspace_repository_id),
        '--caller-run-id','99','--run-id',result.run_id,'--status','succeeded','--publish-requested','false']) == 0
    revision=result.evidence.interest_model_revision
    again=auto_runner.run(args,paths,effective)
    assert again.status=='succeeded' and again.evidence.interest_model_revision==revision
    evidence=json.loads((paths.state/'runs'/f'filter-diagnostic-{again.run_id}.json').read_text())
    assert evidence['lifecycle_gate'].startswith('reuse_')
    invalid=result.model_dump();invalid['confirmed_profile_revision']='f'*40
    with pytest.raises(ValueError):AutoRunResult.model_validate(invalid)
    sidecar=json.loads((target/'latent-deployment-evidence-v1.json').read_text())
    from .test_github_pages_rss import rss
    envelope=dict(schema_name='zotwatch-latent-auto-workflow-envelope',schema_version=1,
        caller_run_id='99',workspace_repository_id=result.evidence.workspace_repository_id,
        run_id=result.run_id,result_status=result.status,evidence=result.evidence.model_dump())
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('final/latent-auto-result-v1.json',data)
        z.writestr('final/latent-deployment-evidence-v1.json',json.dumps(sidecar))
        z.writestr('latent-auto-workflow-envelope-v1.json',json.dumps(envelope))
    archive=stream.getvalue()
    def github(path):
        if path.endswith('/artifacts?per_page=100'):
            return {'artifacts':[dict(name=rss.ARTIFACT,expired=False,id=7,digest='sha256:'+hashlib.sha256(archive).hexdigest())]}
        return dict(repository=dict(full_name='owner/private',id=result.evidence.workspace_repository_id),path='.github/workflows/p5c4-lifecycle.yml',status='completed',conclusion='success')
    monkeypatch.setattr(rss,'github',github);monkeypatch.setattr(rss,'request',lambda *a:archive);monkeypatch.setenv('GITHUB_TOKEN','unused')
    loaded=rss.load_final('owner/private',99)
    assert loaded==result.model_dump()
    feed,n=rss.merge_feed(loaded,None,'https://example.com/feed.xml',datetime.now(timezone.utc),resolve=lambda _: 'Public abstract')
    assert n==1 and len(rss.read_feed(feed))==1
    assert revision.encode() not in feed
