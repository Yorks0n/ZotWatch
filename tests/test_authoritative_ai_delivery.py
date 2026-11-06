"""Authoritative recommendation order and optional AI failure isolation."""
import copy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from zotwatch import optional_llm as llm, staging_ai
from zotwatch.interests.auto_ranking import select
from zotwatch.interests.auto_results import AutoRunResult
from .test_missing_abstract_delivery import configuration, FakeClient, PAPERS


@pytest.fixture
def saved_run(tmp_path, monkeypatch):
    center = lambda i: f"00000000-0000-4000-8000-{i:012d}"
    rows = [dict(work_key=f"10.1234/a{i}", primary_center_id=center(0), center_cosine=.99-i*.006)
        for i in range(25)]
    rows += [dict(work_key=f"10.1234/b{i}", primary_center_id=center(i+1), center_cosine=.7)
        for i in range(19)]
    for row in rows:
        row.update(title=row["work_key"], score=row["center_cosine"], url=None, source="crossref",
            identifier=row["work_key"], doi=row["work_key"], venue="Journal", published="2026-10-27")
    admitted = [r for r in rows if r["work_key"] != "10.1234/a0"]
    final = [r for r in select(rows, limit=None) if r["work_key"] != "10.1234/a0"][:20]
    centers = sorted({r["primary_center_id"] for r in rows})
    evidence = dict(workspace_repository_id=42, library_identity_sha256="b"*64,
        interest_model_revision="c"*32, encoder_fingerprint="d"*64,
        embedding_text_fingerprint="d"*64, formal_center_ids=centers)
    result = AutoRunResult.model_validate(dict(run_id="a"*32, status="succeeded", command="watch",
        exit_code=0, reason="LATENT_AUTO_READY", evidence=evidence, recommendations=final)).model_dump()
    state = tmp_path/"state"
    generation_path = state/"latent-state-v1"/"generations"/evidence["interest_model_revision"]
    generation_path.mkdir(parents=True)
    generation_file, model_file = generation_path/"generation.json", generation_path/"model.json"
    generation_file.write_text("{}")
    model_file.write_text("{}")
    generation = SimpleNamespace(model_revision="c"*32, library_identity_sha256="b"*64,
        workspace_repository_id=42, input_sha256="e"*64,
        model_sha256=sha256(model_file.read_bytes()).hexdigest(),
        input_records=[SimpleNamespace(key="history", title="PRIVATE history", abstract="PRIVATE abstract")],
        centers=[SimpleNamespace(interest_id=c, representative_items=["history"]) for c in centers])
    model = SimpleNamespace(interest_model_revision="c"*32, source_snapshot_sha256="e"*64,
        embedding_text_fingerprint="d"*64, threshold=.55, centers=generation.centers)
    from zotwatch.interests import lifecycle, center_recall_contract
    monkeypatch.setattr(lifecycle.Generation, "model_validate", lambda _: generation)
    monkeypatch.setattr(center_recall_contract, "load_center_model", lambda _: model)
    diagnostic = dict(run_id=result["run_id"], admitted_ranking=admitted,
        candidates={r["work_key"]:dict(title=r["title"], abstract="Verified public abstract.") for r in rows},
        recall_decisions=[dict(candidate_id=r["work_key"], recalled=True, threshold=.55,
            interest_model_revision="c"*32, best_match=dict(interest_id=r["primary_center_id"],
                center_cosine=r["center_cosine"])) for r in rows])
    result_file = tmp_path/"result.json"
    diagnostic_file = state/"runs"/f"filter-diagnostic-{result['run_id']}.json"
    diagnostic_file.parent.mkdir()
    metadata_file = state/"metadata-cache-v1.json"
    metadata_file.write_text(json.dumps(dict(schema_name="zotwatch-metadata-cache", schema_version=1,
        workspace_repository_id=42, entries={})))
    result_file.write_text(json.dumps(result))
    diagnostic_file.write_text(json.dumps(diagnostic))
    return SimpleNamespace(result=result, diagnostic=diagnostic, generation=generation, state=state,
        result_file=result_file, diagnostic_file=diagnostic_file, metadata_file=metadata_file,
        generation_file=generation_file, model_file=model_file,
        args=(result_file, diagnostic_file, metadata_file, generation_file, model_file))


def test_preprint_filter_does_not_reselect_or_change_final_members(saved_run):
    final = saved_run.result["recommendations"]
    reranked = select(saved_run.diagnostic["admitted_ranking"], limit=None)[:20]
    assert {r["work_key"] for r in reranked} != {r["work_key"] for r in final}
    # Unused candidates and their diagnostics cannot prevent final papers translating.
    saved_run.diagnostic["recall_decisions"].append(dict(candidate_id="unused", recalled=True))
    saved_run.diagnostic_file.write_text(json.dumps(saved_run.diagnostic))
    result = llm.prepare(*saved_run.args)
    assert [r["work_key"] for r in result["candidates"]] == [r["work_key"] for r in final]
    assert [r["latent_auto_rank"] for r in result["candidates"]] == list(range(1, 21))
    assert all(r["abstract"] and not r.get("quality_skip_reason") for r in result["candidates"])
    assert result["preparation_warnings"] == []


@pytest.mark.parametrize("fault", ["missing_diagnostic", "wrong_run", "bad_metadata", "foreign_metadata",
    "missing_generation", "foreign_generation", "corrupt_generation", "wrong_recall"])
def test_auxiliary_evidence_failure_keeps_authoritative_translation_inputs(saved_run, fault):
    s = saved_run
    if fault == "missing_diagnostic":
        s.diagnostic_file.unlink()
    elif fault == "wrong_run":
        s.diagnostic["run_id"] = "f"*32
        s.diagnostic_file.write_text(json.dumps(s.diagnostic))
    elif fault == "bad_metadata":
        s.metadata_file.write_text("[]")
    elif fault == "foreign_metadata":
        value = json.loads(s.metadata_file.read_text())
        value["workspace_repository_id"] = 99
        s.metadata_file.write_text(json.dumps(value))
    elif fault == "missing_generation":
        s.generation_file.unlink()
    elif fault == "foreign_generation":
        s.generation.workspace_repository_id = 99
    elif fault == "corrupt_generation":
        s.generation_file.write_text("invalid-json")
    elif fault == "wrong_recall":
        s.diagnostic["recall_decisions"][1]["best_match"]["center_cosine"] = .55
        s.diagnostic_file.write_text(json.dumps(s.diagnostic))
    source = llm.prepare(*s.args)
    assert [r["work_key"] for r in source["candidates"]] == [r["work_key"] for r in s.result["recommendations"]]
    assert source["preparation_warnings"]
    if fault in {"missing_diagnostic", "wrong_run"}:
        assert all(not r["abstract"] for r in source["candidates"])
    else:
        assert all(r["abstract"] for r in source["candidates"])
    if fault in {"foreign_generation", "missing_generation", "corrupt_generation"}:
        assert not source["contexts"]
        assert all(r["quality_skip_reason"] for r in source["candidates"])


def test_invalid_authoritative_identity_still_rejects(saved_run):
    value = saved_run.result
    value["recommendations"].append(copy.deepcopy(value["recommendations"][0]))
    saved_run.result_file.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        llm.prepare(*saved_run.args)


def test_quality_exception_does_not_cancel_translation(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("private diagnostic detail must not reach logs")
    monkeypatch.setattr(staging_ai, "shadow", broken)
    # The conservative fallback uses the same quality function with the route disabled.
    actual_shadow = llm.shadow
    monkeypatch.setattr(staging_ai, "shadow",
        lambda config, *a, **kw: actual_shadow(config, *a, **kw) if not config.features.shadow.enabled else broken())
    monkeypatch.setattr(staging_ai, "translate",
        lambda cfg, rows, path, mode: llm.translate(cfg, rows, path, mode=mode, client_factory=FakeClient))
    final = dict(status="succeeded", command="watch", run_id="a"*32,
        recommendations=[dict(work_key=r["work_key"]) for r in PAPERS])
    sidecar, quality, translations = staging_ai.integrate(configuration(),
        dict(candidates=copy.deepcopy(PAPERS), contexts={}), final, tmp_path)
    assert quality["fallback_original_top20"]
    assert quality["metadata"]["warning_code"] == "AI_QUALITY_UNAVAILABLE"
    assert translations["status_counts"] == {"succeeded": 2}
    assert all(r["title_zh"] for r in sidecar["papers"])


def test_successful_translations_survive_unwritable_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "save_json", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    translated = llm.translate(configuration(), PAPERS, tmp_path/"cache", client_factory=FakeClient)
    assert translated["status_counts"] == {"succeeded": 2}
    assert translated["metadata"]["cache_write_failed"]


def test_diagnostic_write_failure_preserves_public_projection(saved_run, tmp_path, monkeypatch):
    document = configuration().model_dump()
    document["features"]["shadow"] = dict(enabled=False)
    document["features"]["translation"]["budget"] = dict(max_requests=20)
    config = llm.LocalConfig.model_validate(document)
    config_file = tmp_path/"config.json"
    config_file.write_text(config.model_dump_json())
    actual_save = staging_ai.save_json
    def save(path, document):
        if path.parent.name == "ai":
            raise OSError("private diagnostic write failed")
        actual_save(path, document)
    monkeypatch.setattr(staging_ai, "save_json", save)
    monkeypatch.setattr(staging_ai, "translate",
        lambda cfg, rows, path, mode: llm.translate(cfg, rows, path, mode=mode, client_factory=FakeClient))
    summary = staging_ai.run(config_file, saved_run.result_file, saved_run.state, tmp_path/"delivery")
    assert summary["translation_status_counts"] == {"succeeded": 20}
    assert summary["warning_codes"] == ["AI_DIAGNOSTIC_WRITE_FAILED"]
    assert (tmp_path/"delivery"/staging_ai.SIDECAR).exists()


def test_unavoidable_fallback_logs_safe_location_and_actions_warning(tmp_path, monkeypatch, capsys):
    def broken(*args):
        raise ValueError("PRIVATE secret and input must never be logged")
    monkeypatch.setattr(staging_ai, "run", broken)
    assert staging_ai.main(["--config", str(tmp_path/"config"), "--result", str(tmp_path/"result"),
        "--state", str(tmp_path/"state"), "--destination", str(tmp_path/"delivery")]) == 0
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and "::warning::AI_DELIVERY_FALLBACK" in output
    summary = json.loads(output.splitlines()[-1])
    assert summary["error_type"] == "ValueError"
    assert summary["failure_location"].endswith(":broken")
