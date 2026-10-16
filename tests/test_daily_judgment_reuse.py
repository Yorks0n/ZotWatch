"""Daily Top 20 overlap reuses judgments independent of today's rank/batch position."""
import json
from types import SimpleNamespace

import pytest

from zotwatch import optional_llm as llm, jev


def papers(ids):
    return [dict(work_key=f'doi:{i}', title=f'Paper {i}', abstract='Concrete experimental findings. '*20,
        venue='Journal', primary_center_id='center', latent_auto_rank=rank,
        final_recommended=True, interest_profile='Research mechanisms and methods.',
        nearest_representatives=[dict(title=f'History {j}', abstract='Historical findings.') for j in range(3)])
        for rank, i in enumerate(ids, 1)]


@pytest.mark.parametrize('provider', ['llm', 'jev'])
def test_twenty_today_ten_seen_yesterday_only_ten_sent(tmp_path, monkeypatch, provider):
    monkeypatch.setenv('LLM_API_KEY', 'test-llm-key')
    monkeypatch.setenv('LLM_BASE_URL', 'https://example.org/v1')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-jev-key')
    monkeypatch.delenv('LLM_API_FORMAT', raising=False)
    sent = []
    def post(url, **kwargs):
        payload = kwargs['json']
        if provider == 'llm':
            records = json.loads(payload['messages'][1]['content'])
            sent.extend(r['title'] for r in records)
            results = [dict(id=r['id'], decision='keep', relevance='strong', contribution='substantial', reason='相关') for r in records]
            data = dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(dict(results=results))))])
        else:
            sent.extend(r['title'] for r in payload['state'].values())
            answer = dict(type='choice', choice='adjacent_keep', confidence=.9,
                probabilities={key: float(key == 'adjacent_keep') for key in jev.CRITERIA})
            data = dict(model='jev-latest', answers={key: answer for key in payload['questions']})
        return SimpleNamespace(status_code=200, content=b'{}', json=lambda: data)
    monkeypatch.setattr(llm.requests, 'post', post)
    config = llm.LocalConfig.model_validate(dict(services={'judge': dict(model='judge-model')},
        features={'shadow': dict(enabled=True, provider=provider, service='judge' if provider == 'llm' else None,
            batch_size=2, budget=dict(max_requests=12, max_input_tokens=30000))}, allow_private_context=True))
    path = tmp_path/'cache.json'
    first = llm.shadow(config, papers(range(20)), {}, path)
    assert first['metadata']['cache_hits'] == 0 and len(sent) == 20
    yesterday = {r['work_key']: r['decision'] for r in first['judgments']}
    sent.clear()
    # Previously judged papers move to new ranks and different batch positions.
    today = papers(list(range(20, 30)) + list(reversed(range(10, 20))))
    second = llm.shadow(config, today, {}, path)
    assert second['metadata']['cache_hits'] == 10
    assert second['metadata']['requests'] == 5
    assert sent == [f'Paper {i}' for i in range(20, 30)]
    assert second['filtered_recommendations'] == today
    for row in second['judgments'][10:]:
        assert row['llm_status'] == 'cached' and row['decision'] == yesterday[row['work_key']]
    sent.clear()
    third = llm.shadow(config, today, {}, path)
    assert third['metadata']['cache_hits'] == 20 and third['metadata']['requests'] == 0 and sent == []
    # A changed abstract invalidates only that paper, not the other nineteen.
    today[0]['abstract'] += ' Additional result.'
    changed = llm.shadow(config, today, {}, path)
    assert changed['metadata']['cache_hits'] == 19 and sent == ['Paper 20']
