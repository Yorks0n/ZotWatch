from copy import deepcopy
from types import SimpleNamespace
import pytest
from src.models import ZoteroItem
from src.storage import ProfileStorage
from src.build_profile import ProfileBuilder
from zotwatch.interests.suggestions import MAX_ITEMS, Projection, export_summary, project, write_projection
from zotwatch.interests.results import TopicRunResult


def item(key, tag="Graph Neural Networks", title="Molecular prediction", abstract="Interpretable molecular prediction"):
    return ZoteroItem(key=key, version=1, title=title, abstract=abstract,
        tags=[tag], creators=["Private Author"], raw={"private": "not exported"})


def test_synthetic_sqlite_projection_and_independent_export(tmp_path):
    storage = ProfileStorage(tmp_path / "profile.sqlite"); storage.initialize()
    for key in ("PRIVATE_A", "PRIVATE_B", "PRIVATE_C"):
        storage.upsert_item(item(key), key)
    builder = ProfileBuilder(tmp_path, storage, SimpleNamespace(), vectorizer=object())
    projection = builder.suggest_interests()
    assert projection.sampled_items == 3 and not projection.truncated
    assert len(projection.suggestions) == 1
    suggestion = projection.suggestions[0]
    assert suggestion.supporting_items == 3
    assert suggestion.source_types == ["tag", "text_keyword"]
    assert "graph neural networks" in suggestion.keywords
    for forbidden in ("PRIVATE_", "Private Author", "not exported", "Interpretable molecular prediction", "centroid", "embedding"):
        assert forbidden not in projection.model_dump_json()
    state = tmp_path / "state"; run_id = "a" * 32
    write_projection(state, run_id, projection)
    result = TopicRunResult(run_id=run_id, command="profile", status="succeeded", reason="PROFILE_BUILT", exit_code=0)
    machine = tmp_path / "machine.json"; machine.write_text(result.model_dump_json() + "\n")
    (state / "runs" / f"topic-{run_id}.json").write_bytes(machine.read_bytes())
    confirmed = tmp_path / "feedback/interests/v1/profile.json"; confirmed.parent.mkdir(parents=True); confirmed.write_text("confirmed user correction")
    summary = export_summary(state, machine, tmp_path / "summary", engine_sha="b" * 40,
        workspace_repository_id=123, caller_run_id=456, caller_run_attempt=1)
    assert summary.projection == projection
    assert [p.name for p in (tmp_path / "summary").iterdir()] == ["interest-suggestions-v1.json"]
    assert confirmed.read_text() == "confirmed user correction"
    assert builder.suggest_interests() == projection
    storage.close()


def test_bounded_deterministic_support_and_weak_evidence():
    assert project([item("a")]).suggestions == []
    assert project([item("same"), item("same")]).suggestions == []
    assert project([item("a", "Research", "Study methods", "Results analysis"), item("b", "Research", "Study methods", "Results analysis")]).suggestions == []
    assert project([item(str(i), "", "这是未经分词的私人标题", "这是不应输出的原始摘要片段") for i in range(2)]).suggestions == []
    assert project([item(str(i), "图神经网络", "", "") for i in range(2)]).suggestions[0].keywords == ["图神经网络"]
    items = [item(str(i), "  GRAPH   Neural Networks ") for i in range(3)]
    assert project(items) == project(reversed(items))
    projection = project(item(str(i)) for i in range(MAX_ITEMS + 100))
    assert projection.truncated and projection.sampled_items == MAX_ITEMS
    assert all(s.supporting_items <= MAX_ITEMS for s in projection.suggestions)
    rich = [item(f"{i}-{j}", f"Topic {chr(97+i)}", f"Distinctive{chr(97+i)}", "") for i in range(20) for j in range(2)]
    assert len(project(rich).suggestions) <= 12
    assert all(len(s.keywords) <= 5 for s in project(rich).suggestions)


def test_closed_summary_rejects_private_fields_and_invalid_counts():
    p = project([item("a"), item("b")]).model_dump()
    bad = deepcopy(p); bad["suggestions"][0]["item_keys"] = ["SECRET"]
    with pytest.raises(ValueError): Projection.model_validate(bad)
    bad = deepcopy(p); bad["suggestions"][0]["supporting_items"] = 3
    with pytest.raises(ValueError): Projection.model_validate(bad)
    bad = deepcopy(p); bad["suggestions"][0]["keywords"] = ["https://private.example"]
    with pytest.raises(ValueError): Projection.model_validate(bad)


def test_current_run_export_does_not_reuse_old_projection(tmp_path):
    state = tmp_path / "state"; state.mkdir(); run_id = "c" * 32
    result = TopicRunResult(run_id=run_id, command="profile", status="succeeded", reason="PROFILE_BUILT", exit_code=0)
    machine = tmp_path / "machine.json"; machine.write_text(result.model_dump_json()+"\n")
    (state / "runs").mkdir(); (state / "runs" / f"topic-{run_id}.json").write_bytes(machine.read_bytes())
    write_projection(state, "d" * 32, project([item("a"), item("b")]))
    with pytest.raises(FileNotFoundError):
        export_summary(state, machine, tmp_path / "summary", engine_sha="b" * 40,
                       workspace_repository_id=123, caller_run_id=456, caller_run_attempt=1)


def test_profile_only_before_confirmation_and_rebuild_preserve_confirmed_git(tmp_path, monkeypatch):
    from src import cli as engine
    from zotwatch.interests import runner
    from zotwatch.interests.git import read_local
    from .test_interests_p5b1 import setup_topic
    from .helpers import FixedVectors
    paths, effective = setup_topic(tmp_path, monkeypatch)
    original = read_local(tmp_path)
    def ingest(self, **kwargs):
        self.storage.initialize()
        for key in ("SYNTH_A", "SYNTH_B", "SYNTH_C"):
            self.storage.upsert_item(item(key), key)
        self.storage.set_library_identity_sha256(kwargs["library_identity_sha256"])
        self.storage.set_last_modified_version(1)
    monkeypatch.setattr(engine.ZoteroIngestor, "run", ingest)
    class SyntheticVectors(FixedVectors):
        def encode(self, texts):
            import numpy as np
            return np.asarray([[1., 0., 0.] for _ in texts], dtype="float32")
    monkeypatch.setattr(engine.build_profile_module, "TextVectorizer", SyntheticVectors)
    args = SimpleNamespace(command="profile", full=True, weekly=False)
    before_confirmation = runner.run(args, paths, effective, snapshot_loader=lambda _: None)
    assert before_confirmation.status == "succeeded" and before_confirmation.evidence is None
    first = Projection.model_validate_json((paths.state / "runs" / f"suggestions-{before_confirmation.run_id}.json").read_bytes())
    rebuilt = runner.run(args, paths, effective)
    assert rebuilt.status == "succeeded"
    second = Projection.model_validate_json((paths.state / "runs" / f"suggestions-{rebuilt.run_id}.json").read_bytes())
    assert second == first and first.suggestions
    assert read_local(tmp_path) == original
