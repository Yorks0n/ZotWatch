"""Generation reuse, true member-neighbor retrieval and private durable storage."""
import copy
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from zotwatch import center_profiles as profiles, optional_llm as llm, jev


def fixture():
    vectors = {}
    for i in range(12):
        v = np.zeros(384); v[0] = np.cos(i / 20); v[1] = np.sin(i / 20)
        vectors[str(i)] = v
    centroid = vectors['0']
    centers = [NS(interest_id='c', member_keys=list(vectors), centroid=centroid)]
    generation = NS(workspace_repository_id=42, library_identity_sha256='library', model_revision='revision',
        centers=centers, input_records=[NS(key=k, title='History '+k,
        abstract='Specific experimental methods and biological findings. '*8) for k in vectors])
    papers = [dict(work_key='paper', title='Candidate', abstract='Specific research findings. '*20,
        primary_center_id='c', venue='Journal', latent_auto_rank=1, final_recommended=True)]
    source = dict(candidates=papers, contexts={'c': []})
    cfg = llm.LocalConfig.model_validate(dict(services={'llm': {'model': 'text-model'}},
        features={'translation': {'enabled': False, 'selected_connection': 'llm'},
            'shadow': {'enabled': True, 'provider': 'jev'}}, allow_private_context=True))
    return cfg, source, generation, vectors


def route(cfg, provider):
    doc = cfg.model_dump()
    doc['features']['shadow']['provider'] = provider
    doc['features']['shadow']['service'] = 'llm' if provider == 'llm' else None
    return llm.LocalConfig.model_validate(doc)


class Client:
    calls = []
    def __init__(self, service, budget):
        assert service.model == 'text-model'
    def complete(self, prompt, records):
        self.calls.append(records)
        return [dict(id='center', description='关注生物机制及实验方法。')]


def enrich(tmp_path, cfg, source, generation, vectors, client=Client):
    return profiles.enrich(cfg, source, generation, None, vectors, None, tmp_path/profiles.NAME,
        encode=lambda *a: (np.asarray([vectors['11']]), {}), client_factory=client)


def test_portrait_once_all_members_nearest_three_and_generation_invalidation(tmp_path):
    cfg, source, generation, vectors = fixture(); Client.calls = []
    before = copy.deepcopy(source)
    enriched = enrich(tmp_path, cfg, source, generation, vectors)
    assert source == before
    assert len(Client.calls) == 1
    assert [r['title'] for r in Client.calls[0][0]['representatives']] == ['History '+str(i) for i in range(10)]
    row = enriched['candidates'][0]
    assert [r['title'] for r in row['nearest_representatives']] == ['History 11', 'History 10', 'History 9']
    assert row['interest_profile'] == '关注生物机制及实验方法。'
    # Choosing another judge or changing model does not regenerate the same center portrait.
    cfg = route(cfg, 'llm')
    enrich(tmp_path, cfg, source, generation, vectors)
    assert len(Client.calls) == 1
    generation.model_revision = 'new-revision'
    enrich(tmp_path, cfg, source, generation, vectors)
    assert len(Client.calls) == 2
    generation.library_identity_sha256 = 'another-library'
    enrich(tmp_path, cfg, source, generation, vectors)
    assert len(Client.calls) == 3


def test_disabled_and_no_private_context_make_no_requests(tmp_path):
    cfg, source, generation, vectors = fixture(); Client.calls = []
    cfg = cfg.model_copy(update={'allow_private_context': False})
    assert enrich(tmp_path, cfg, source, generation, vectors) is source
    cfg = route(cfg.model_copy(update={'allow_private_context': True}), 'none')
    assert enrich(tmp_path, cfg, source, generation, vectors) is source
    assert Client.calls == [] and not (tmp_path/profiles.NAME).exists()


def test_partial_profile_success_survives_and_only_missing_is_retried(tmp_path):
    cfg, source, generation, vectors = fixture()
    generation.centers.append(NS(interest_id='c2', member_keys=list(vectors), centroid=vectors['0']))
    class Partial(Client):
        calls = []
        def complete(self, prompt, records):
            if len(self.calls) == 1:
                raise llm.LLMError('api_unavailable')
            return super().complete(prompt, records)
    with pytest.raises(llm.LLMError):
        enrich(tmp_path, cfg, source, generation, vectors, Partial)
    assert set(json.loads((tmp_path/profiles.NAME).read_text())['profiles']) == {'c'}
    Client.calls = []
    enrich(tmp_path, cfg, source, generation, vectors)
    assert len(Client.calls) == 1


@pytest.mark.parametrize('provider', ['llm', 'jev'])
def test_both_judges_receive_portrait_journal_and_candidate_specific_neighbors(tmp_path, monkeypatch, provider):
    cfg, source, generation, vectors = fixture(); Client.calls = []
    source = enrich(tmp_path, cfg, source, generation, vectors)
    cfg = route(cfg, provider)
    captured = []
    class Judge:
        endpoint = 'https://example.org/v1'
        def __init__(self, *args): pass
        def complete(self, prompt, records):
            captured.extend(records)
            return [dict(id=r['id'], decision='keep', relevance='strong', contribution='substantial', reason='相关') for r in records]
    if provider == 'jev':
        def native(config, records, cache):
            captured.extend(records)
            return {r['id']: dict(status='succeeded', result=dict(decision='keep')) for r in records}, 'evaluated', {}
        monkeypatch.setattr(jev, 'evaluate_jev', native)
    result = llm.shadow(cfg, source['candidates'], source['contexts'], tmp_path/'quality.json', client_factory=Judge)
    assert result['after_count'] == 1
    assert captured[0]['journal'] == 'Journal'
    assert captured[0]['interest_profile'] == '关注生物机制及实验方法。'
    assert [r['title'] for r in captured[0]['representatives']] == ['History 11', 'History 10', 'History 9']


def test_vector_integrity_and_cache_scope(tmp_path):
    cfg, source, generation, vectors = fixture(); Client.calls = []
    enrich(tmp_path, cfg, source, generation, vectors)
    with pytest.raises(ValueError, match='scope'):
        profiles.ProfileCache.validate((tmp_path/profiles.NAME).read_bytes(), 43)
    (tmp_path/'embeddings.npy').write_bytes(b'corrupt')
    generation.embeddings_sha256 = 'wrong'
    with pytest.raises(ValueError, match='checksum'):
        profiles.load_vectors(generation, tmp_path)


def test_profile_route_never_uses_jev():
    with pytest.raises(ValueError):
        llm.LocalConfig.model_validate(dict(services={'jev': {'provider': 'jev'}}, profile_service='jev'))
    cfg, *_ = fixture()
    assert profiles.profile_service(cfg).model == 'text-model'


def test_durable_portrait_roundtrip_and_conflict(tmp_path):
    import base64
    from hashlib import sha1
    from zotwatch.profile_transport import ProfileGitState
    cfg, source, generation, vectors = fixture(); Client.calls = []
    enrich(tmp_path, cfg, source, generation, vectors)
    remote = object.__new__(ProfileGitState); remote.repository_id = 42
    state = {'head': None, 'blobs': {}, 'tree': None}
    def api(method, path, body=None, missing=False):
        if path == '/git/ref/heads/' + remote.branch:
            return {'object': {'sha': state['head']}} if state['head'] else None
        if path == '/git/blobs':
            data = base64.b64decode(body['content'])
            sha = sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            state['blobs'][sha] = dict(encoding='base64', content=body['content'], size=len(data))
            return {'sha': sha}
        if path.startswith('/git/blobs/'):
            return state['blobs'][path.rsplit('/', 1)[1]]
        if path == '/git/trees':
            state['tree'] = body['tree']
            return {'sha': 'tree'}
        if path == '/git/trees/tree':
            return dict(truncated=False, tree=[{**r, 'size': state['blobs'][r['sha']]['size']} for r in state['tree']])
        if method == 'GET' and path.startswith('/git/commits/'):
            return {'tree': {'sha': 'tree'}}
        if path == '/git/commits':
            return {'sha': 'commit'}
        if path == '/git/refs':
            assert body['ref'] == 'refs/heads/zotwatch-center-profiles-v1'
            state['head'] = body['sha']; return {}
        raise AssertionError(path)
    remote.api = api
    remote.restore(tmp_path)
    remote.publish(tmp_path)
    restored = tmp_path/'restored'
    remote.restore(restored)
    assert (restored/profiles.NAME).read_bytes() == (tmp_path/profiles.NAME).read_bytes()
    assert remote.publish(restored)['unchanged']
    state['head'] = 'concurrent-change'
    with pytest.raises(ValueError, match='CAS conflict'):
        remote.publish(restored)


def test_profile_failure_omits_ai_projection_and_keeps_original(tmp_path, monkeypatch):
    from zotwatch import staging_ai
    def failed(*args):
        raise llm.LLMError('portrait_llm_not_configured')
    monkeypatch.setattr(staging_ai, 'run', failed)
    assert staging_ai.main(['--config', str(tmp_path/'config'), '--result', str(tmp_path/'result'),
        '--state', str(tmp_path/'state'), '--destination', str(tmp_path/'output')]) == 0
    assert not (tmp_path/'output'/staging_ai.SIDECAR).exists()


def test_unchanged_profile_avoids_llm_even_without_credentials(tmp_path):
    cfg, source, generation, vectors = fixture(); Client.calls = []
    enrich(tmp_path, cfg, source, generation, vectors)
    def forbidden(*args):
        pytest.fail('Existing portrait must not initialize LLM')
    enrich(tmp_path, cfg, source, generation, vectors, forbidden)


def test_jev_payload_contains_profile_as_untrusted_state(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs['json'])
        answer = dict(type='choice', choice='direct_keep', confidence=.9,
            probabilities={key: 1.0 if key == 'direct_keep' else 0.0 for key in jev.CRITERIA})
        data = dict(model='jev-latest', answers={'p1': answer})
        return NS(status_code=200, content=b'{}', json=lambda: data)
    monkeypatch.setattr(jev.requests, 'post', post)
    client = jev.JevClient('jev-latest', llm.FeatureBudgetConfig())
    row = dict(id='p1', title='Candidate', abstract='Evidence', journal='Journal',
        interest_profile='Mechanisms', representatives=[dict(title='Nearest', abstract='History')])
    client.complete([row])
    assert captured[0]['state']['p1'] == {k:v for k,v in row.items() if k != 'id'}
    assert 'portraits' in captured[0]['questions']['p1']['instructions']
