"""Only the staging delivery boundary and conservative exclusion guard."""
import copy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from zotwatch import jev, optional_llm as llm, staging_ai

spec = importlib.util.spec_from_file_location('staging_rss', Path(__file__).parents[1] / 'scripts/github_pages_rss.py')
rss = importlib.util.module_from_spec(spec);spec.loader.exec_module(rss)


def native_answer(choice, p, confidence):
    others = [k for k in jev.CRITERIA if k != choice]
    return dict(type='choice', choice=choice, confidence=confidence,
        probabilities={choice:p, **{k:(1-p)/len(others) for k in others}})


@pytest.mark.parametrize('choice,p,confidence,abstract,decision', [
    ('mismatch_exclude', .85, .7, 'Concrete study. ' * 30, 'exclude'),
    ('mismatch_exclude', .7, .64, 'Concrete study. ' * 30, 'review'),
    ('mismatch_exclude', .95, .9, '', 'review'),
    ('no_contribution_exclude', .95, .9, 'Concrete study. ' * 30, 'review')])
def test_only_supported_high_certainty_interest_mismatch_excludes(choice,p,confidence,abstract,decision):
    assert jev.conservative_decision(native_answer(choice,p,confidence), {'abstract':abstract})[0] == decision


def test_disabled_staging_never_prepares_or_reads_private_generation(tmp_path, monkeypatch):
    def forbidden(*args): raise AssertionError('disabled path must bypass AI')
    monkeypatch.setattr(staging_ai, 'prepare', forbidden)
    result=staging_ai.run(tmp_path/'missing-config.yaml', tmp_path/'missing-result.json',tmp_path/'state',tmp_path/'delivery')
    assert result['status']=='disabled' and result['requests']==0
    assert not (tmp_path/'delivery').exists()


def test_staging_jev_then_translation_public_projection_and_idempotent_rss(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','jev-secret')
    monkeypatch.setenv('LLM_API_KEY','llm-secret')
    monkeypatch.setenv('LLM_BASE_URL','https://api.example/v1')
    papers=[dict(work_key=f'10.1234/{i}',title=f'Paper {i}',venue='Journal',abstract='Concrete public findings. '*20,
        primary_center_id='PRIVATE-center',latent_auto_rank=i,final_recommended=True) for i in (1,2)]
    source=dict(candidates=papers,contexts={'PRIVATE-center':[dict(title='PRIVATE title',abstract='PRIVATE history')]})
    final=dict(run_id='a'*32,status='succeeded',command='watch',recommendations=[dict(work_key=r['work_key'],title=r['title'],venue='Journal') for r in papers])
    before=copy.deepcopy(final)
    calls=[]
    def post(url, **kw):
        calls.append(kw['json']['model'])
        if url==jev.ENDPOINT:
            data=dict(model='jev-resolved',answers={k:native_answer('mismatch_exclude' if k=='p1' else 'adjacent_keep',.9,.8) for k in kw['json']['questions']})
        else:
            assert kw['json']['model']=='translator' # no llm judgment model
            records=json.loads(kw['json']['messages'][1]['content'])
            data={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'results':[
                dict(id=r['id'],title_zh='中文标题',abstract_zh='中文研究发现。') for r in records]})}}]}
        return SimpleNamespace(status_code=200,content=b'json',json=lambda:data)
    monkeypatch.setattr(llm.requests,'post',post)
    cfg=llm.LocalConfig.model_validate(dict(services={'translator':{'model':'translator'}},features={
        'translation':{'enabled':True,'service':'translator','budget':{'max_input_tokens':30000}},
        'shadow':{'enabled':True,'provider':'jev','budget':{'max_input_tokens':30000}}},allow_private_context=True))
    sidecar,quality,translations=staging_ai.integrate(cfg,source,final,tmp_path/'cache')
    assert final==before and quality['after_count']==1 and sidecar['retained_work_keys']==['10.1234/2']
    assert calls==['jev-latest','translator']
    assert 'PRIVATE' not in json.dumps(sidecar) and 'confidence' not in json.dumps(sidecar)
    projected=rss.apply_delivery_sidecar(final,sidecar)
    feed,n=rss.merge_feed(projected,None,'https://example.org/feed.xml',resolve=lambda _:pytest.fail('must reuse verified public abstract'))
    assert n==1 and len(rss.read_feed(feed))==1 and '中文研究发现' in feed.decode()
    assert 'Paper 2 | 中文标题' in feed.decode() and b'PRIVATE' not in feed
    assert rss.merge_feed(projected,feed,'https://example.org/feed.xml')==(feed,0)
    staging_ai.integrate(cfg,source,final,tmp_path/'cache')
    assert calls==['jev-latest','translator'] # independent successful cache reuse


def test_invalid_delivery_falls_back_without_reranking_or_private_fields():
    final=dict(run_id='a'*32,status='succeeded',command='watch',recommendations=[dict(work_key='10.1234/a',title='Original')])
    source_hash=sha256(json.dumps(final,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    sidecar=dict(schema_name='zotwatch-staging-rss-ai',schema_version=1,run_id=final['run_id'],source_final_sha256=source_hash,
        retained_work_keys=['10.1234/a'],papers=[dict(work_key='10.1234/a',title_en='Original',abstract_en='Public',title_zh=None,abstract_zh=None)])
    sidecar['papers'][0]['representatives']='PRIVATE'
    assert rss.apply_delivery_sidecar(final,sidecar) is final
    del sidecar['papers'][0]['representatives'];sidecar['retained_work_keys']=['10.1234/unknown']
    assert rss.apply_delivery_sidecar(final,sidecar) is final
    sidecar['retained_work_keys']=['10.1234/a'];sidecar['source_final_sha256']='0'*64
    assert rss.apply_delivery_sidecar(final,sidecar) is final
