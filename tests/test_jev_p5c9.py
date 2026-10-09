"""Native request contract, single-provider isolation, consent, cache and fallback."""
import copy
import json
from types import SimpleNamespace

import pytest

from zotwatch import optional_llm as llm, jev

PAPERS = [dict(work_key='doi:a', title='Single-cell perturbation model', venue='Journal A',
    abstract='A causal prediction method.', primary_center_id='c', latent_auto_rank=1, final_recommended=True),
    dict(work_key='doi:b', title='Salt stress response', venue='Journal B', abstract='',
         primary_center_id='c', latent_auto_rank=2, final_recommended=True)]
CONTEXTS = {'c': [dict(title='Private representative', abstract='Historical study.' * 100)]}


def config(provider='jev', **updates):
    doc = dict(features={'shadow': {'enabled': True, 'provider': provider, 'batch_size': 2,
               'budget': {'max_requests': 12, 'max_input_tokens': 30000}}}, allow_private_context=True)
    doc.update(updates)
    return llm.LocalConfig.model_validate(doc)


def answer(choice):
    return dict(type='choice', choice=choice, confidence=.9,
                probabilities={k: 1. if k == choice else 0. for k in jev.CRITERIA})


@pytest.fixture
def native_http(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'secret-native-key')
    # Jev must not consult any LLM environment connection.
    for k in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL'):
        monkeypatch.delenv(k, raising=False)
    calls = []
    def post(url, **kw):
        calls.append((url, copy.deepcopy(kw)))
        data = dict(model='jev-resolved', answers={k: answer('adjacent_keep' if r['abstract'] else 'evidence_review')
            for k, r in kw['json']['state'].items()}, usage={'input_tokens': 123, 'output_tokens': 12})
        return SimpleNamespace(status_code=200, content=b'json', json=lambda: data)
    monkeypatch.setattr(jev.requests, 'post', post)
    return calls


def no_llm(*args):
    raise AssertionError('unselected LLM initialized')


def test_native_shape_privacy_and_success_cache(tmp_path, native_http):
    before = copy.deepcopy(PAPERS)
    path = tmp_path / 'jev-cache.json'
    first = llm.shadow(config(), PAPERS, CONTEXTS, path, client_factory=no_llm)
    second = llm.shadow(config(), PAPERS, CONTEXTS, path, client_factory=no_llm)
    assert PAPERS == before and first['filtered_recommendations'] == PAPERS
    assert first['decision_counts'] == {'keep': 1, 'review': 1}
    assert first['metadata']['usage'] == {'input_tokens': 123, 'output_tokens': 12}
    assert second['metadata']['requests'] == 0 and second['metadata']['cache_hits'] == 2
    url, request = native_http[0]
    assert url == jev.ENDPOINT and request['allow_redirects'] is False
    assert request['headers'] == {'Authorization': 'Bearer secret-native-key'}
    payload = request['json']
    assert set(payload) == {'model', 'state', 'questions'} and payload['model'] == 'jev-latest'
    assert set(payload['state']) == {'p1', 'p2'} and payload['state']['p1']['journal'] == 'Journal A'
    assert len(payload['state']['p1']['representatives'][0]['abstract']) == 900
    assert 'doi:' not in json.dumps(payload) and 'secret-native-key' not in json.dumps(first)
    assert first['judgments'][0]['reason_source'].startswith('local mapping')
    assert first['judgments'][0]['relevance'] == 'not_assessed'
    changed = copy.deepcopy(PAPERS); changed[0]['venue'] = 'Journal C'
    assert llm.shadow(config(), changed, CONTEXTS, path)['metadata']['cache_hits'] == 1
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(jev, 'VERSION', 'new-rules')
        assert llm.shadow(config(), PAPERS, CONTEXTS, path)['metadata']['cache_hits'] == 0
    finally:
        monkeypatch.undo()
    changed_config = config().model_dump();changed_config['features']['shadow']['model'] = 'jev-other'
    assert llm.shadow(llm.LocalConfig.model_validate(changed_config), PAPERS, CONTEXTS, path)['metadata']['cache_hits'] == 0


@pytest.mark.parametrize('provider,consent,enabled', [('none', True, True), ('jev', False, True), ('jev', True, False)])
def test_none_disabled_and_missing_consent_never_initialize_clients(tmp_path, monkeypatch, provider, consent, enabled):
    monkeypatch.setattr(jev, 'JevClient', no_llm)
    cfg = config(provider, allow_private_context=consent, features={'shadow': {'enabled': enabled, 'provider': provider}})
    output = llm.shadow(cfg, PAPERS, CONTEXTS, tmp_path / 'cache.json', client_factory=no_llm)
    assert output['metadata']['requests'] == 0 and output['filtered_recommendations'] == PAPERS
    assert not (tmp_path / 'cache.json').exists()


@pytest.mark.parametrize('failure', ['invalid_probabilities', 'http_error', 'missing_key', 'credential_echo'])
def test_native_failure_restores_full_original_without_llm(tmp_path, monkeypatch, native_http, failure):
    if failure == 'missing_key':
        monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    else:
        def post(url, **kw):
            values = {k: answer('mismatch_exclude') for k in kw['json']['questions']}
            if failure == 'invalid_probabilities':
                next(iter(values.values()))['probabilities'] = {'invented': 1}
            data = dict(model='secret-native-key' if failure == 'credential_echo' else 'jev-resolved', answers=values)
            return SimpleNamespace(status_code=401 if failure == 'http_error' else 200, content=b'json', json=lambda: data)
        monkeypatch.setattr(jev.requests, 'post', post)
    output = llm.shadow(config(), PAPERS, CONTEXTS, tmp_path / 'cache.json', client_factory=no_llm)
    assert output['fallback_original_top20'] and output['filtered_recommendations'] == PAPERS
    assert output['excluded'] == [] and not (tmp_path / 'cache.json').exists()
    assert 'secret-native-key' not in json.dumps(output)


def test_llm_selection_never_initializes_jev(tmp_path, monkeypatch):
    monkeypatch.setattr(jev, 'JevClient', no_llm)
    class ExistingClient:
        endpoint = 'https://example.com'
        def __init__(self, service, budget): pass
        def complete(self, prompt, records):
            return [dict(id=r['id'], decision='keep', relevance='moderate', contribution='incremental', reason='Useful work.') for r in records]
    cfg = config('llm', services={'judge': {'model': 'existing'}}, features={'shadow': {'enabled': True, 'provider': 'llm', 'service': 'judge'}})
    result = llm.shadow(cfg, PAPERS, CONTEXTS, tmp_path / 'cache.json', client_factory=ExistingClient)
    assert result['metadata']['provider'] == 'openai-compatible' and not result['fallback_original_top20']


def test_partial_failure_keeps_success_cache_but_applies_no_exclusions(tmp_path, monkeypatch, native_http):
    cfg = config(features={'shadow': {'enabled': True, 'provider': 'jev', 'batch_size': 1}})
    def post(url, **kw):
        if kw['json']['state']['p1']['abstract'] == '':
            return SimpleNamespace(status_code=422, content=b'error')
        data = dict(model='jev-resolved', answers={'p1': answer('mismatch_exclude')})
        return SimpleNamespace(status_code=200, content=b'json', json=lambda: data)
    monkeypatch.setattr(jev.requests, 'post', post)
    path = tmp_path / 'cache.json'
    output = llm.shadow(cfg, PAPERS, CONTEXTS, path, client_factory=no_llm)
    assert output['filtered_recommendations'] == PAPERS and output['excluded'] == []
    assert len(json.loads(path.read_text())['entries']) == 1
    again = llm.shadow(cfg, PAPERS, CONTEXTS, path, client_factory=no_llm)
    assert again['metadata']['cache_hits'] == 1 and again['fallback_original_top20']


def test_native_transient_retry_uses_backoff_and_never_other_provider(tmp_path, monkeypatch, native_http):
    good_post = jev.requests.post
    attempts, waits = [], []
    def post(url, **kw):
        attempts.append(url)
        if len(attempts) == 1:
            return SimpleNamespace(status_code=429, content=b'error')
        return good_post(url, **kw)
    monkeypatch.setattr(jev.requests, 'post', post)
    monkeypatch.setattr(jev.time, 'sleep', waits.append)
    cfg = config(features={'shadow': {'enabled': True, 'provider': 'jev', 'retry_once': True,
                 'budget': {'max_input_tokens': 30000}}})
    output = llm.shadow(cfg, PAPERS, CONTEXTS, tmp_path / 'cache.json', client_factory=no_llm)
    assert not output['fallback_original_top20']
    assert output['metadata']['requests'] == 2 and output['metadata']['retries'] == 1
    assert output['metadata']['failed_attempts'] == 1 and waits == [2]
    assert set(attempts) == {jev.ENDPOINT}
