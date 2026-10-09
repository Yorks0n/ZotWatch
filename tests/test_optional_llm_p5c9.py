"""Only local P5C9 boundaries: consent, fallback, identity, cache, and API shape."""
import copy
import json
from types import SimpleNamespace

import pytest

from zotwatch import optional_llm as llm


@pytest.fixture(autouse=True)
def isolated_llm_environment(monkeypatch):
    for key in ("LLM_API_FORMAT", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
                "LLM_TRANSLATION_MODEL", "LLM_SHADOW_MODEL"):
        monkeypatch.delenv(key, raising=False)


def configuration(**updates):
    doc = dict(services={"translate": {"model": "translation-model"},
                         "judge": {"model": "judge-model"}},
        features={"translation": {"enabled": True, "service": "translate", "batch_size": 2},
                  "shadow": {"enabled": True, "service": "judge", "batch_size": 2}},
        allow_private_context=True)
    doc.update(updates)
    return llm.LocalConfig.model_validate(doc)


PAPERS = [dict(work_key="doi:a", title="Gene ABC1 increased by 2.5-fold", abstract="Observed at 25 °C.",
    primary_center_id="center-a", center_cosine=.7, latent_auto_rank=1, final_recommended=True),
    dict(work_key="doi:b", title="Method comparison", abstract="", primary_center_id="center-a",
         center_cosine=.6, latent_auto_rank=2, final_recommended=True)]
CONTEXTS = {"center-a": [dict(title="PRIVATE title", abstract="PRIVATE abstract " * 200)]}


class FakeClient:
    endpoint = "https://fake.example/v1"
    calls = []

    def __init__(self, service, budget):
        self.model = service.model

    def complete(self, prompt, records):
        self.calls.append((self.model, copy.deepcopy(records)))
        if prompt == llm.TRANSLATION_PROMPT:
            return [dict(id=r["id"], title_zh="ABC1 增加 2.5 倍", abstract_zh="25 °C" if r["abstract"] else "") for r in records]
        return [dict(id=r["id"], relevance="weak" if r["id"] == "p1" else "strong",
            decision="exclude" if r["id"] == "p1" else "keep",
            contribution="unclear" if r["id"] == "p1" else "incremental", reason="摘要证据有限。") for r in records]


def no_client(*args):
    raise AssertionError("must not initialize any API client")


def test_disabled_and_english_have_no_network_or_cache(tmp_path):
    config = llm.LocalConfig()
    path = tmp_path / "cache.json"
    result = llm.translate(config, PAPERS, path, client_factory=no_client)
    assert result["papers"][0]["title_en"] == PAPERS[0]["title"]
    assert result["papers"][0]["status"] == "disabled"
    assert not path.exists()
    english = llm.translate(configuration(), PAPERS, path, mode="english", client_factory=no_client)
    assert english["metadata"]["requests"] == 0
    assert english["papers"][0]["title_zh"] is None


def test_consent_is_required_even_when_shadow_enabled(tmp_path):
    result = llm.shadow(configuration(allow_private_context=False), PAPERS, CONTEXTS,
        tmp_path / "cache.json", client_factory=no_client)
    assert all(r["llm_status"] == "private_context_not_enabled" for r in result["judgments"])
    assert result["metadata"]["requests"] == 0
    assert not (tmp_path / "cache.json").exists()


def test_missing_key_returns_original_and_uncertain(tmp_path, monkeypatch):
    for key in ("LLM_API_KEY",):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    result = llm.translate(configuration(), PAPERS, tmp_path / "cache.json")
    assert all(r["status"] == "missing_key" for r in result["papers"])
    assert result["papers"][0]["abstract_en"] == PAPERS[0]["abstract"]
    judged = llm.shadow(configuration(), PAPERS, CONTEXTS, tmp_path / "cache.json")
    assert all(r["relevance"] == "uncertain" and r["llm_status"] == "missing_key" for r in judged["judgments"])


def test_batch_cache_bound_to_text_model_endpoint_and_prompt(tmp_path, monkeypatch):
    FakeClient.calls = []
    config, path = configuration(), tmp_path / "cache.json"
    first = llm.translate(config, PAPERS, path, client_factory=FakeClient)
    assert first["metadata"]["requests"] == 1
    assert first["papers"][1]["abstract_zh"] == ""
    second = llm.translate(config, PAPERS, path, client_factory=FakeClient)
    assert second["metadata"]["cache_hits"] == 2 and len(FakeClient.calls) == 1
    changed = copy.deepcopy(PAPERS)
    changed[0]["abstract"] += " Changed input."
    assert llm.translate(config, changed, path, client_factory=FakeClient)["metadata"]["cache_hits"] == 1
    doc = config.model_dump()
    doc["services"]["translate"]["model"] = "another-model"
    assert llm.translate(llm.LocalConfig.model_validate(doc), PAPERS, path, client_factory=FakeClient)["metadata"]["cache_hits"] == 0
    monkeypatch.setattr(FakeClient, "endpoint", "https://different.example/v1")
    assert llm.translate(config, PAPERS, path, client_factory=FakeClient)["metadata"]["cache_hits"] == 0
    monkeypatch.setattr(llm, "TRANSLATION_VERSION", "next-prompt")
    assert llm.translate(config, PAPERS, path, client_factory=FakeClient)["metadata"]["cache_hits"] == 0


def test_shadow_sends_only_bounded_text_and_never_mutates_ranking(tmp_path):
    before = copy.deepcopy(PAPERS)
    FakeClient.calls = []
    result = llm.shadow(configuration(), PAPERS, CONTEXTS, tmp_path / "cache.json", client_factory=FakeClient)
    assert PAPERS == before
    model, payload = FakeClient.calls[0]
    assert model == "judge-model"
    assert set(payload[0]) == {"id", "title", "abstract", "abstract_truncated", "representatives"}
    assert payload[0]["id"] == "p1"
    assert "doi:" not in json.dumps(payload)
    assert len(payload[0]["representatives"][0]["abstract"]) == 900
    assert payload[0]["representatives"][0]["abstract_truncated"]
    assert result["filtered_recommendations"][0]["work_key"] == "doi:b"
    assert result["filtered_recommendations"][0]["latent_auto_rank"] == 2
    assert result["after_count"] == 1 and result["excluded"][0]["reason"]
    assert [r["work_key"] for r in result["judgments"]] == ["doi:a", "doi:b"]
    assert result["shadow_only"]


def test_item_and_request_budgets_preserve_original_on_fallback(tmp_path):
    doc = configuration().model_dump()
    doc["features"]["translation"].update(batch_size=1,
        budget=llm.FeatureBudgetConfig(max_items=2, max_requests=1).model_dump())
    papers = copy.deepcopy(PAPERS) + [{**PAPERS[0], "work_key": "doi:c"}]
    result = llm.translate(llm.LocalConfig.model_validate(doc), papers,
                           tmp_path / "cache.json", client_factory=FakeClient)
    assert result["metadata"]["requests"] == 1
    assert [r["status"] for r in result["papers"]] == ["succeeded", "request_budget_exceeded", "item_budget_exceeded"]
    assert result["papers"][2]["title_en"] == PAPERS[0]["title"]


def test_body_like_source_stays_local_and_only_title_is_sent(tmp_path):
    papers = copy.deepcopy(PAPERS[:1])
    papers[0]["abstract"] = "Private body-like source. " * 400
    FakeClient.calls = []
    result = llm.translate(configuration(), papers, tmp_path / "cache.json", client_factory=FakeClient)
    assert FakeClient.calls[0][1][0]["abstract"] == ""
    row = result["papers"][0]
    assert row["abstract_en"] == papers[0]["abstract"]
    assert row["status"] == "source_text_requires_review"
    assert row["title_zh"] and row["abstract_zh"] is None


@pytest.mark.parametrize("failure", ["api_unavailable", "wrong_id", "invalid_enum"])
def test_failures_are_not_cached_or_reported_as_success(tmp_path, failure):
    class BadClient(FakeClient):
        def complete(self, prompt, records):
            if failure == "api_unavailable":
                raise llm.LLMError("api_unavailable")
            return [dict(id="wrong" if failure == "wrong_id" else r["id"],
                relevance="invented" if failure == "invalid_enum" else "strong",
                decision="keep", contribution="substantial", reason="reason") for r in records]
    result = llm.shadow(configuration(), PAPERS, CONTEXTS, tmp_path / "cache.json", client_factory=BadClient)
    assert all(r["relevance"] == "uncertain" and r["contribution"] == "unclear" for r in result["judgments"])
    assert not (tmp_path / "cache.json").exists()
    assert result["filtered_recommendations"] == PAPERS
    assert result["fallback_original_top20"]


def test_http_shape_and_key_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "private-test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, content=b"response", json=lambda: {"choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps({"results": [
                dict(id="doi:a", title_zh="private-test-key", abstract_zh="text")]})}}]})
    monkeypatch.setattr(llm.requests, "post", post)
    result = llm.translate(configuration(), PAPERS[:1], tmp_path / "cache.json")
    assert result["papers"][0]["status"] == "credential_in_response"
    assert "private-test-key" not in json.dumps(result)
    assert not (tmp_path / "cache.json").exists()
    assert calls[0][0] == "https://api.deepseek.com/chat/completions"
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["json"]["response_format"] == {"type": "json_object"}
    assert "private-test-key" not in json.dumps(calls[0][1]["json"])


def test_environment_connection_has_generic_names_and_independent_models(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "private-test-key")
    monkeypatch.setenv("LLM_MODEL", "shared-model")
    monkeypatch.setenv("LLM_TRANSLATION_MODEL", "translation-model")
    service = llm.LocalService(model_env="LLM_TRANSLATION_MODEL")
    assert llm.connection(service) == ("https://api.example/v1", "private-test-key", "translation-model", "openai")
    assert llm.connection(llm.LocalService(model_env="LLM_SHADOW_MODEL"))[2] == "shared-model"
    monkeypatch.setenv("LLM_BASE_URL", "https://user:password@example.com/v1")
    with pytest.raises(llm.LLMError, match="missing_or_invalid_endpoint"):
        llm.connection(service)


def test_anthropic_messages_format(monkeypatch):
    monkeypatch.setenv("LLM_API_FORMAT", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example")
    monkeypatch.setenv("LLM_API_KEY", "private-test-key")
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        content = json.dumps({"results": [dict(id="a", relevance="strong", decision="keep", contribution="substantial", reason="evidence")]})
        return SimpleNamespace(status_code=200, content=b"response", json=lambda: {
            "stop_reason": "end_turn", "content": [{"type": "text", "text": content}]})
    monkeypatch.setattr(llm.requests, "post", post)
    client = llm.CompatibleClient(llm.LocalService(model="judge-model"), llm.FeatureBudgetConfig())
    assert client.complete(llm.SHADOW_PROMPT, [{"id": "a", "title": "title"}])[0]["relevance"] == "strong"
    url, request = calls[0]
    assert url == "https://api.example/v1/messages"
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    assert request["headers"]["x-api-key"] == "private-test-key"
    assert "system" in request["json"] and request["json"]["messages"][0]["role"] == "user"
    assert "response_format" not in request["json"]


def test_html_escapes_paper_and_judgment_text(tmp_path):
    papers = copy.deepcopy(PAPERS)
    papers[0]["title"] = "<script>do not run</script>"
    result = llm.translate(llm.LocalConfig(), papers, tmp_path / "cache.json")
    assert "<script>" not in llm.render_html(result)
    assert "&lt;script&gt;" in llm.render_html(result)


def test_quality_filter_only_final_no_reordering_review_and_incremental_kept(tmp_path):
    class ReviewClient(FakeClient):
        def complete(self, prompt, records):
            self.calls.append((self.model, copy.deepcopy(records)))
            return [dict(id=r["id"], decision="review" if r["id"] == "p1" else "keep",
                         relevance="moderate", contribution="incremental", reason="相邻研究，保留核查。") for r in records]
    papers = copy.deepcopy(PAPERS) + [{**PAPERS[0], "work_key": "doi:c", "latent_auto_rank": 21, "final_recommended": False}]
    result = llm.shadow(configuration(), papers, CONTEXTS, tmp_path / "cache.json", client_factory=ReviewClient)
    assert result["before_count"] == result["after_count"] == 2
    assert result["filtered_recommendations"] == PAPERS
    assert result["excluded"] == []
    assert all(r["contribution"] == "incremental" for r in result["judgments"])


def test_partial_api_failure_restores_entire_top20(tmp_path):
    class PartialClient(FakeClient):
        def complete(self, prompt, records):
            if records[0]["title"] == PAPERS[1]["title"]:
                raise llm.LLMError("api_unavailable")
            return [dict(id=records[0]["id"], decision="exclude", relevance="weak",
                         contribution="unclear", reason="表面词汇关联。")]
    doc = configuration().model_dump();doc["features"]["shadow"]["batch_size"] = 1
    result = llm.shadow(llm.LocalConfig.model_validate(doc), PAPERS, CONTEXTS, tmp_path / "cache.json", client_factory=PartialClient)
    assert result["fallback_original_top20"]
    assert result["filtered_recommendations"] == PAPERS and result["excluded"] == []


def test_one_retry_success_is_cached(tmp_path):
    class TransientClient(FakeClient):
        attempts = 0
        def complete(self, prompt, records):
            self.attempts += 1
            if self.attempts == 1:
                raise llm.LLMError("api_unavailable")
            return super().complete(prompt, records)
    doc = configuration().model_dump();doc["features"]["shadow"]["retry_once"] = True
    result = llm.shadow(llm.LocalConfig.model_validate(doc), PAPERS, CONTEXTS, tmp_path / "cache.json", client_factory=TransientClient)
    assert result["metadata"]["retries"] == 1 and result["metadata"]["requests"] == 2
    assert not result["fallback_original_top20"]


def test_translation_cleanup_preserves_numbers_and_genes():
    text = "摘要 摘要 背景 背景 SlHSFA2调控405个基因。结果 结果 引导编辑与角质/木栓质。"
    cleaned = llm.cleanup_translation(text)
    assert "摘要" not in cleaned and "结果 结果" not in cleaned and "背景 背景" not in cleaned
    assert "SlHSFA2 调控 405 个" in cleaned
    assert "先导编辑（prime editing）" in cleaned and "角质素/木栓质" in cleaned


def test_metadata_refinement_is_independent_and_top20_only():
    class Metadata:
        def crossref(self, doi):return {'DOI':doi}
        def europe_pmc(self, doi):return 'Verified replacement abstract.'
        def openalex(self, doi):raise AssertionError('must stop after reliable replacement')
    paper={**PAPERS[0], 'work_key':'10.1234/example', 'abstract':'Body. ' * 1500,
           'abstract_source':'openalex'}
    source=dict(translations=[paper], candidates=[paper,PAPERS[1]], contexts=CONTEXTS)
    before=copy.deepcopy(source)
    result=llm.refine_metadata(source,providers=Metadata())
    assert source==before
    assert result['translations'][0]['abstract']=='Verified replacement abstract.'
    assert result['candidates']==result['translations']
    assert result['metadata_repairs'][0]['status']=='replaced'
