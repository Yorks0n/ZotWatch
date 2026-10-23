"""Missing abstracts are repaired or skipped per paper across daily AI delivery."""
import copy
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from zotwatch import optional_llm as llm, staging_ai, center_profiles
from zotwatch.metadata import Cache, resolve
spec = importlib.util.spec_from_file_location("missing_abstract_rss", Path(__file__).parents[1]/"scripts/github_pages_rss.py")
rss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rss)
PAPERS = [dict(work_key=f"10.1234/p{i}", title=f"Paper {i}", abstract="Verified public findings.",
    primary_center_id="center", latent_auto_rank=i, final_recommended=True) for i in (1, 2)]
CONTEXTS = {"center": [dict(title="Private history", abstract="Historical findings.")]}


def configuration():
    return llm.LocalConfig.model_validate(dict(services={
        "translator": dict(model="translator"), "judge": dict(model="judge")},
        features=dict(translation=dict(enabled=True, service="translator"),
            shadow=dict(enabled=True, provider="llm", service="judge")), allow_private_context=True))


def no_client(*args):
    pytest.fail("Skipped papers must not initialize an API client")


class FakeClient:
    endpoint = "https://example.org/v1"
    calls = []

    def __init__(self, service, budget):
        self.model = service.model

    def complete(self, prompt, records):
        self.calls.append((self.model, copy.deepcopy(records)))
        if prompt == llm.TRANSLATION_PROMPT:
            return [dict(id=r["id"], title_zh="中文标题", abstract_zh="中文摘要") for r in records]
        return [dict(id=r["id"], decision="exclude", relevance="weak", contribution="unclear",
            reason="Unrelated research.") for r in records]


class Client:
    def __init__(self, *args):
        pass

    def complete(self, prompt, records):
        return [dict(id="center", description="Research mechanisms.")]


def fixture():
    import numpy as np
    vectors = {}
    for i in range(12):
        v = np.zeros(384)
        v[0], v[1] = np.cos(i/20), np.sin(i/20)
        vectors[str(i)] = v
    generation = SimpleNamespace(workspace_repository_id=42, library_identity_sha256="library",
        model_revision="revision", centers=[SimpleNamespace(interest_id="center",
            member_keys=list(vectors), centroid=vectors["0"])],
        input_records=[SimpleNamespace(key=k, title="History "+k, abstract="Historical findings.") for k in vectors])
    return configuration(), dict(candidates=[{**PAPERS[0], "work_key": "paper"}],
        contexts=CONTEXTS), generation, vectors


class Metadata:
    def __init__(self):
        self.calls = []

    def crossref(self, doi):
        self.calls.append((doi, "crossref"))
        return {"DOI": doi}

    def europe_pmc(self, doi):
        self.calls.append((doi, "europe_pmc"))
        return "Recovered public abstract." if doi.endswith("/recovered") else None

    def openalex(self, doi):
        self.calls.append((doi, "openalex"))
        return None


def source():
    rows = [{**PAPERS[0], "work_key": "10.1234/recovered", "abstract": ""},
            {**PAPERS[1], "work_key": "10.1234/missing", "abstract": ""},
            {**PAPERS[1], "work_key": "10.1234/good", "latent_auto_rank": 3}]
    return dict(translations=rows, candidates=copy.deepcopy(rows), contexts=CONTEXTS)


def test_repair_missing_abstracts_uses_provider_chain_and_reuses_negative_cache(tmp_path):
    original = source()
    before = copy.deepcopy(original)
    providers = Metadata()
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    result = llm.refine_metadata(original, providers=providers, now=now, cache=Cache(tmp_path, 42))
    assert original == before
    assert [r["status"] for r in result["metadata_repairs"]] == ["replaced", "no_verified_abstract"]
    assert result["candidates"][0]["abstract"] == "Recovered public abstract."
    assert result["candidates"][1]["abstract"] == ""
    assert providers.calls == [
        ("10.1234/recovered", "crossref"), ("10.1234/recovered", "europe_pmc"),
        ("10.1234/missing", "crossref"), ("10.1234/missing", "europe_pmc"),
        ("10.1234/missing", "openalex")]
    providers.calls.clear()
    again = llm.refine_metadata(original, providers=providers, now=now, cache=Cache(tmp_path, 42))
    assert providers.calls == []
    assert all(r["cache_hit"] for r in again["metadata_repairs"])


def test_date_only_cache_does_not_suppress_supplementary_fetch(tmp_path):
    providers = Metadata()
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    cache = Cache(tmp_path, 42)
    cache.entries["10.1234/recovered"] = resolve("10.1234/recovered", now, providers, need_abstract=False)
    providers.calls.clear()
    repaired = llm.refine_metadata(source(), providers=providers, now=now, cache=cache)
    assert repaired["candidates"][0]["abstract"] == "Recovered public abstract."
    assert ("10.1234/recovered", "europe_pmc") in providers.calls


def test_malformed_metadata_for_one_paper_does_not_stop_later_repairs():
    class Malformed(Metadata):
        def crossref(self, doi):
            if doi.endswith("/missing"):
                return []
            return super().crossref(doi)

    inputs = source()
    inputs["translations"].reverse()
    repaired = llm.refine_metadata(inputs, providers=Malformed())
    assert [r["status"] for r in repaired["metadata_repairs"]] == ["no_verified_abstract", "replaced"]
    assert repaired["candidates"][-1]["abstract"] == "Recovered public abstract."


@pytest.mark.parametrize("mode", ["bilingual", "chinese"])
def test_missing_paper_skips_api_and_budget_without_blocking_other_translations(tmp_path, mode):
    papers = source()["candidates"]
    doc = configuration().model_dump()
    doc["features"]["translation"]["budget"] = dict(max_items=1, max_requests=1)
    cfg = llm.LocalConfig.model_validate(doc)
    FakeClient.calls = []
    result = llm.translate(cfg, papers, tmp_path/"translations", mode=mode, client_factory=FakeClient)
    assert [r["status"] for r in result["papers"]] == ["no_verified_abstract", "no_verified_abstract", "succeeded"]
    assert result["metadata"]["requests"] == 1
    assert len(FakeClient.calls[0][1]) == 1
    assert FakeClient.calls[0][1][0]["title"] == papers[2]["title"]
    assert result["papers"][0]["title_zh"] is None
    assert llm.translate(cfg, papers[:2], tmp_path/"empty", client_factory=no_client)["metadata"]["requests"] == 0


def test_missing_abstract_skips_neighbors_and_quality_without_disabling_valid_exclusions(tmp_path):
    cfg, original, generation, vectors = fixture()
    rows = [{**original["candidates"][0], "work_key": "missing", "abstract": ""},
            {**original["candidates"][0], "latent_auto_rank": 2}]
    original["candidates"] = rows
    before = copy.deepcopy(original)
    encoded = []

    def encode(model, cache, papers):
        import numpy as np
        encoded.extend(papers)
        return np.asarray([vectors["11"]]), {}

    result = center_profiles.enrich(cfg, original, generation, None, vectors, None, tmp_path/"profiles",
        encode=encode, client_factory=Client)
    assert original == before
    assert [r["id"] for r in encoded] == ["paper"]
    assert [r["work_key"] for r in result["candidates"]] == ["missing", "paper"]
    assert "nearest_representatives" not in result["candidates"][0]
    cfg = configuration()
    quality = llm.shadow(cfg, result["candidates"], {}, tmp_path/"quality", client_factory=FakeClient)
    assert quality["judgments"][0]["llm_status"] == "no_verified_abstract"
    assert quality["judgments"][0]["decision"] == "review"
    assert [r["work_key"] for r in quality["filtered_recommendations"]] == ["missing"]
    assert not quality["fallback_original_top20"]
    assert center_profiles.enrich(cfg, {"candidates": rows[:1]}, generation, None, {}, None,
        tmp_path/"no-profiles", encode=no_client, client_factory=no_client)["candidates"] == rows[:1]


@pytest.mark.parametrize("provider", ["none", "llm", "jev", "portrait_failure"])
def test_daily_run_repairs_then_translates_and_publishes_other_papers(tmp_path, monkeypatch, provider):
    inputs = source()
    final = dict(run_id="a"*32, status="succeeded", command="watch",
        evidence=dict(interest_model_revision="revision", workspace_repository_id=42),
        recommendations=[dict(work_key=r["work_key"], title=r["title"]) for r in inputs["candidates"]])
    result_path = tmp_path/"result.json"
    result_path.write_text(json.dumps(final))
    doc = configuration().model_dump()
    doc["features"]["shadow"] = dict(enabled=provider != "none", provider="llm" if provider == "portrait_failure" else provider,
        **(dict(service="judge") if provider in {"llm", "portrait_failure"} else {}))
    doc["output_mode"] = "chinese"
    config = llm.LocalConfig.model_validate(doc)
    config_path = tmp_path/"config.json"
    config_path.write_text(json.dumps(config.model_dump()))
    monkeypatch.setattr(staging_ai, "prepare", lambda *a: copy.deepcopy(inputs))
    if provider != "none":
        import zotwatch.interests.lifecycle
        import zotwatch.interests.center_recall_contract
        generation = tmp_path/"state"/"latent-state-v1"/"generations"/"revision"
        generation.mkdir(parents=True)
        (generation/"generation.json").write_text("{}")
        monkeypatch.setattr(zotwatch.interests.lifecycle.Generation, "model_validate", lambda *a: None)
        monkeypatch.setattr(zotwatch.interests.center_recall_contract, "load_center_model", lambda *a: None)
        monkeypatch.setattr(center_profiles, "load_vectors", lambda *a: {})

        def enrich(config, repaired, *args):
            assert repaired["candidates"][0]["abstract"] == "Recovered public abstract."
            if provider == "portrait_failure":
                raise llm.LLMError("api_unavailable")
            return repaired

        monkeypatch.setattr(center_profiles, "enrich", enrich)
    providers = Metadata()
    import zotwatch.metadata
    monkeypatch.setattr(zotwatch.metadata, "Providers", lambda: providers)
    monkeypatch.setenv("LLM_API_KEY", "synthetic-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.org/v1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-jev-key")
    sent = []

    def post(url, **kw):
        if "questions" in kw["json"]:
            from zotwatch.jev import CRITERIA
            assert len(kw["json"]["state"]) == 2
            return SimpleNamespace(status_code=200, content=b"{}", json=lambda: {
                "model": "jev-latest", "answers": {
                    k: dict(type="choice", choice="adjacent_keep", confidence=.9,
                        probabilities={key: float(key == "adjacent_keep") for key in CRITERIA})
                    for k in kw["json"]["questions"]}})
        records = json.loads(kw["json"]["messages"][1]["content"])
        if "Translate academic" not in kw["json"]["messages"][0]["content"]:
            assert len(records) == 2 and all(r["abstract"] for r in records)
            values = [dict(id=r["id"], decision="keep", relevance="strong", contribution="substantial",
                reason="Keep this paper.") for r in records]
            return SimpleNamespace(status_code=200, content=b"{}", json=lambda: {
                "choices": [dict(finish_reason="stop", message=dict(content=json.dumps(dict(results=values))))]})
        sent.extend(records)
        return SimpleNamespace(status_code=200, content=b"{}", json=lambda: {
            "choices": [dict(finish_reason="stop", message=dict(content=json.dumps(dict(results=[
                dict(id=r["id"], title_zh="中文标题", abstract_zh="中文摘要") for r in records]))))]})

    monkeypatch.setattr(llm.requests, "post", post)
    summary = staging_ai.run(config_path, result_path, tmp_path/"state", tmp_path/"delivery")
    assert summary["status"] == "evaluated"
    assert summary["translation_status_counts"] == {"succeeded": 2, "no_verified_abstract": 1}
    assert summary["metadata_repair_counts"] == {"replaced": 1, "no_verified_abstract": 1}
    assert summary["fallback_original_top20"] == (provider == "portrait_failure")
    assert len(sent) == 2 and all(r["abstract"] for r in sent)
    sidecar = json.loads((tmp_path/"delivery"/staging_ai.SIDECAR).read_text())
    assert sidecar["papers"][1]["title_zh"] is None
    projected = rss.apply_delivery_sidecar(final, sidecar)
    feed, count = rss.merge_feed(projected, None, "https://example.org/feed.xml", resolve=lambda _: None)
    items = rss.read_feed(feed)
    assert count == 3
    assert items["urn:doi:10.1234/recovered"]["title"] == "中文标题"
    assert items["urn:doi:10.1234/good"]["title"] == "中文标题"
    assert items["urn:doi:10.1234/missing"]["title"] == inputs["candidates"][1]["title"]
    assert json.loads(result_path.read_text()) == final
