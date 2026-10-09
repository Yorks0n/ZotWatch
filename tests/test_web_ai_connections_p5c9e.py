"""Web routing must use the selected connection without changing prompts/rules."""
import json
from types import SimpleNamespace
from zotwatch import optional_llm as llm


def test_feature_routes_use_distinct_credentials_endpoints_models(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'private-openai-key')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'private-deepseek-key')
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs['headers']['Authorization'], kwargs['json']['model']))
        rows = json.loads(kwargs['json']['messages'][1]['content'])
        translation = 'Translate academic' in kwargs['json']['messages'][0]['content']
        values = [dict(id=r['id'], **(dict(title_zh='译文', abstract_zh='摘要') if translation else
            dict(decision='keep', relevance='strong', contribution='substantial', reason='符合兴趣。'))) for r in rows]
        return SimpleNamespace(status_code=200, content=b'ok', json=lambda: {'choices':[
            {'finish_reason':'stop','message':{'content':json.dumps({'results':values})}}]})
    monkeypatch.setattr(llm.requests, 'post', post)
    config = llm.LocalConfig.model_validate(dict(services={
        'openai':dict(provider='openai',base_url='https://api.openai.com/v1',api_key_env='OPENAI_API_KEY',model='connection-default'),
        'deepseek':dict(provider='deepseek',base_url='https://api.deepseek.com',api_key_env='DEEPSEEK_API_KEY',model='deepseek-default')},
        features={'translation':dict(enabled=True,service='deepseek',model_override='translation-model'),
            'shadow':dict(enabled=True,provider='llm',service='openai',model_override='judgment-model')},allow_private_context=True))
    row = dict(id='paper',title='Title',abstract='Verified abstract.')
    llm.evaluate_batches(config,'translation',[row],tmp_path/'t.json')
    llm.evaluate_batches(config,'shadow',[row],tmp_path/'s.json')
    assert calls == [('https://api.deepseek.com/chat/completions','Bearer private-deepseek-key','translation-model'),
        ('https://api.openai.com/v1/chat/completions','Bearer private-openai-key','judgment-model')]
    assert 'private-' not in (tmp_path/'t.json').read_text() + (tmp_path/'s.json').read_text()
    config = config.model_copy(update={"allow_private_context": False})
    llm.evaluate_batches(config,'shadow',[row],tmp_path/'s2.json')
    assert len(calls) == 2


def test_english_output_skips_translation_requests(tmp_path):
    config = llm.LocalConfig.model_validate(dict(services={'s':{'model':'m'}},
        features={'translation':dict(enabled=True,service='s')},output_mode='english',output_language='en'))
    result = llm.translate(config,[dict(work_key='p',title='English',abstract='')],tmp_path/'cache',mode=config.output_mode)
    assert result['metadata']['requests'] == 0 and result['papers'][0]['title_en'] == 'English'
