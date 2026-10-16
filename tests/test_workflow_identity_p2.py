import subprocess

import pytest

from zotwatch.workflow.identity import WorkflowIdentityError, verify_workflow_identity
from src import vectorizer as vectorizer_module


def _repository(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", path], check=True)
    (path / "marker").write_text("engine\n", encoding="utf-8")
    subprocess.run(["git", "-C", path, "add", "marker"], check=True)
    subprocess.run(
        [
            "git", "-C", path, "-c", "user.name=P2 Test",
            "-c", "user.email=p2@example.invalid", "commit", "-qm", "fixture",
        ],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", path, "rev-parse", "HEAD"], text=True
    ).strip()


def test_identity_binds_engine_checkout_to_reusable_workflow_sha(tmp_path):
    engine = tmp_path / "engine"
    sha = _repository(engine)

    identity = verify_workflow_identity(
        expected_repository="Yorks0n/ZotWatch",
        workflow_repository="Yorks0n/ZotWatch",
        workflow_sha=sha,
        engine_path=engine,
    )

    assert identity.engine_sha == sha
    assert identity.workflow_repository == "Yorks0n/ZotWatch"


@pytest.mark.parametrize("workflow_sha", ["main", "v2", "a" * 39, "g" * 40])
def test_identity_rejects_non_full_sha(tmp_path, workflow_sha):
    engine = tmp_path / "engine"
    _repository(engine)
    with pytest.raises(WorkflowIdentityError):
        verify_workflow_identity(
            expected_repository="Yorks0n/ZotWatch",
            workflow_repository="Yorks0n/ZotWatch",
            workflow_sha=workflow_sha,
            engine_path=engine,
        )


def test_identity_rejects_wrong_repository_or_checkout(tmp_path):
    engine = tmp_path / "engine"
    sha = _repository(engine)
    with pytest.raises(WorkflowIdentityError):
        verify_workflow_identity(
            expected_repository="Yorks0n/ZotWatch",
            workflow_repository="someone/fork",
            workflow_sha=sha,
            engine_path=engine,
        )
    with pytest.raises(WorkflowIdentityError):
        verify_workflow_identity(
            expected_repository="Yorks0n/ZotWatch",
            workflow_repository="Yorks0n/ZotWatch",
            workflow_sha="0" * 40,
            engine_path=engine,
        )


def test_default_embedding_model_loads_an_immutable_revision(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, name, **kwargs):
            calls.append((name, kwargs))

    monkeypatch.setattr(vectorizer_module, "SentenceTransformer", FakeModel)
    vectorizer_module.TextVectorizer().load()
    assert calls == [
        (
            vectorizer_module.DEFAULT_MODEL_NAME,
            {"revision": vectorizer_module.DEFAULT_MODEL_REVISION},
        )
    ]
