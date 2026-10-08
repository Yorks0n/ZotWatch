from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import tarfile
from types import SimpleNamespace

import numpy as np
import pytest

from src.models import ZoteroItem
from src.storage import ProfileStorage
from src.computational_state import pseudonymous_library_identity, StateCoordinator
from zotwatch.interests import lifecycle as lc, runner, recall_integration
from zotwatch.interests.center_recall_contract import digest
from zotwatch.interests.lifecycle_transport import pack, unpack
from zotwatch.interests.recall_integration import LatentRecallUnavailable
from zotwatch.interests.workflow_results import materialize
from .test_latent_recall_integration_p5b5 import pipeline, integrated_args, works, install_collection, ranking

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
IDENTITY = pseudonymous_library_identity('user', '123456')


def seed(storage, identity=IDENTITY, n=12, revision=10):
    storage.initialize()
    for i in range(n):
        row = ZoteroItem.from_zotero_api({'key': f'K{i:05}', 'version': revision, 'data': {
            'itemType': 'journalArticle', 'title': f'Cell atlas study {i}',
            'abstractNote': 'Cell atlas abstract', 'DOI': f'10.1234/study{i}'}})
        storage.upsert_item(row, row.key)
    storage.set_library_identity_sha256(identity)
    storage.set_last_modified_version(revision)


def fake_encode(model, cache, records):
    vectors = np.zeros((len(records), 384)); vectors[:, 0] = 1.
    return vectors, {'embedding_text_fingerprint': model.embedding_text_fingerprint}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, 'validate_encoder', lambda *a: None)
    monkeypatch.setattr(lc, 'encode_candidates', fake_encode)
    storage = ProfileStorage(tmp_path / 'profile.sqlite')
    seed(storage)
    yield lc.LifecycleStore(tmp_path), storage
    storage.close()


def ensure(pair, **kwargs):
    store, storage = pair
    return store.ensure(storage, 42, IDENTITY, 'unused', now=kwargs.pop('now', NOW), **kwargs)


def test_bootstrap_private_distinct_generation_and_exact_projection(store, tmp_path):
    d, reason, encoded = ensure(store)
    assert (reason, encoded) == ('initial', 12)
    g, _, path = store[0].load(d, 42, IDENTITY)
    assert g.formation['summary']['formal_center_count'] == 1
    assert len(g.centers[0].member_keys) == 12
    assert d.interest_model_revision != '316ccd15ed658f0c3cf89142b4ba026a'
    other = lc.LifecycleStore(tmp_path / 'other')
    d2, _, _ = other.ensure(store[1], 99, IDENTITY, 'unused', now=NOW)
    assert d2.interest_model_revision != d.interest_model_revision
    assert d2.model_sha256 != d.model_sha256
    assert d2.workspace_repository_id == 99
    with pytest.raises(ValueError): other.load(d2, 42, IDENTITY)
    assert sha256(path.read_bytes()).hexdigest() == d.model_sha256


def test_monthly_material_gate_incremental_encoding_lineage_atomic_switch_and_rollback(store):
    first, _, _ = ensure(store)
    seed(store[1], n=13, revision=11)
    assert ensure(store, now=NOW + timedelta(days=29))[1:] == ('reuse_before_monthly_check', 0)
    second, reason, encoded = ensure(store, now=NOW + timedelta(days=31))
    assert (reason, encoded) == ('material_library_change', 1)
    g, _, _ = store[0].load(second, 42, IDENTITY)
    assert g.previous_model_revision == first.interest_model_revision
    assert g.centers[0].interest_id == store[0].load(first, 42, IDENTITY)[0].centers[0].interest_id
    assert second.previous_deployment_revision == first.deployment_revision
    rollback = store[0].rollback(first.deployment_revision, 42, IDENTITY, store[0].token(), 'unused')
    assert rollback.interest_model_revision == first.interest_model_revision
    assert rollback.activation_reason == 'rollback'
    assert rollback.deployment_revision not in (first.deployment_revision, second.deployment_revision)
    assert store[0].load(second, 42, IDENTITY)
    with pytest.raises(ValueError): store[0].rollback(second.deployment_revision, 42, IDENTITY, 'f'*64, 'unused')


def test_monthly_no_material_change_reuses(store):
    first, _, _ = ensure(store)
    assert ensure(store, now=NOW + timedelta(days=31)) == (first, 'reuse_no_material_change', 0)


@pytest.mark.parametrize('failure', ['encoder', 'snapshot', 'cas'])
def test_failure_retains_current_and_previous_generation(store, failure):
    first, _, _ = ensure(store)
    token = store[0].token()
    seed(store[1], n=13, revision=11)
    def encode(model, cache, records):
        if failure == 'encoder': raise ValueError('encoder failed')
        if failure == 'snapshot': store[1].set_last_modified_version(12)
        if failure == 'cas':
            # A competing authority changes the CAS token during computation.
            (store[0].root / 'current.json').write_bytes((store[0].root / 'current.json').read_bytes() + b'\n')
        return fake_encode(model, cache, records)
    with pytest.raises(ValueError): ensure(store, manual=True, encode=encode)
    assert store[0].current() == first
    if failure != 'cas': assert store[0].token() == token
    assert store[0].load(first, 42, IDENTITY)


def test_changed_library_isolated_bootstrap_no_vector_or_uuid_reuse_and_rollback_rejected(store):
    first, _, _ = ensure(store)
    new_id = pseudonymous_library_identity('user', '654321')
    seed(store[1], identity=new_id)
    with pytest.raises(ValueError): store[0].load(first, 42, new_id)
    second, reason, encoded = store[0].ensure(store[1], 42, new_id, 'unused', now=NOW)
    assert (reason, encoded) == ('library_changed', 12)
    g, _, _ = store[0].load(second, 42, new_id)
    assert g.previous_model_revision is None
    assert g.centers[0].interest_id != store[0].load(first, 42, IDENTITY)[0].centers[0].interest_id
    with pytest.raises(ValueError): store[0].rollback(first.deployment_revision, 42, new_id, store[0].token(), 'unused')


@pytest.mark.parametrize('field,value', [('workspace_repository_id', 99), ('library_identity_sha256', 'f'*64),
    ('library_revision', 999), ('input_sha256', 'f'*64), ('model_sha256', 'f'*64), ('interest_model_revision', 'f'*32)])
def test_descriptor_exchange_or_tampering_rejected(store, field, value):
    d, _, _ = ensure(store)
    altered = lc.Deployment.model_validate({**d.model_dump(), field: value})
    with pytest.raises((ValueError, OSError)): store[0].load(altered, 42, IDENTITY)


def test_cross_run_closed_private_archive_restores_independent_of_checkpoint(store, tmp_path):
    d, _, _ = ensure(store)
    archive = pack(store[0], 42)
    restored = unpack(archive, tmp_path / 'next-run', 42)
    assert restored == d
    assert pack(lc.LifecycleStore(tmp_path / 'next-run'), 42) == archive
    with pytest.raises(ValueError): unpack(archive, tmp_path / 'wrong-user', 99)
    assert not (tmp_path / 'wrong-user/latent-state-v1').exists()
    with pytest.raises(ValueError): unpack(archive, tmp_path / 'next-run', 42)
    with pytest.raises((ValueError, tarfile.ReadError, OSError)): unpack(b'bad', tmp_path / 'corrupt', 42)


@pytest.mark.parametrize('n', [0, 5])
def test_no_formal_center_remains_not_ready_without_lowering_policy(store, n):
    store[1].connect().execute('DELETE FROM items'); store[1].connect().commit()
    seed(store[1], n=n)
    d, _, _ = ensure(store)
    assert store[0].load(d, 42, IDENTITY)[0].centers == []
    with pytest.raises(LatentRecallUnavailable): lc.load_runtime(store[0], 42, IDENTITY, 'unused')


def test_actual_encoder_manifest_rejects_missing_and_corrupt_weights(tmp_path):
    with pytest.raises(OSError): lc.validate_encoder(tmp_path)
    enc = lc.accepted_encoder()
    cache = tmp_path / ('models--'+enc.model_identifier.replace('/', '--')) / 'snapshots' / enc.model_revision
    cache.mkdir(parents=True)
    for name in enc.artifact_sha256:
        p=cache/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'wrong')
    with pytest.raises(ValueError): lc.validate_encoder(tmp_path)


def test_profile_bootstrap_then_confirmed_watch_own_model_exact_evidence(pipeline, monkeypatch, tmp_path):
    from src import cli as engine
    from tests.helpers import FixedVectors
    monkeypatch.setattr(FixedVectors, 'encode', lambda self,texts: np.asarray([[1.,0.,0.]]*len(texts),dtype=np.float32))
    from zotwatch.interests.git import read_local
    paths, effective, _, _ = pipeline
    monkeypatch.setattr(lc, 'validate_encoder', lambda *a: None)
    monkeypatch.setattr(lc, 'encode_candidates', fake_encode)
    monkeypatch.setattr(engine.ZoteroIngestor, 'run', lambda self, **kw: seed(self.storage))
    monkeypatch.setattr(__import__('importlib').import_module('zotwatch.runtime.preflight'), 'preflight', lambda *a, **kw: SimpleNamespace(ready=True))
    args = integrated_args(latent_lifecycle='per-user-v1')
    args.command='profile'; args.candidate_policy='confirmed-topic-candidates-v1'
    result=runner.run(args, paths, effective, snapshot_loader=lambda _: None)
    assert (result.status, result.reason) == ('succeeded', 'PROFILE_BUILT')
    install_collection(monkeypatch, works())
    monkeypatch.setattr(recall_integration, 'encode_candidates', fake_encode)
    args.command='watch'; args.candidate_policy='center-recall-v1'; args.full=False
    result=runner.run(args, paths, effective, ranker=ranking)
    assert result.status == 'succeeded', result
    d=lc.LifecycleStore(paths.state).current()
    assert result.evidence.latent_recall.interest_model_revision == d.interest_model_revision
    dest=tmp_path/'operational'
    materialize(paths.state/'runs'/f'topic-{result.run_id}.json',0,paths.state,dest)
    sidecar=lc.RunDeploymentEvidence.model_validate_json((dest/'latent-deployment-evidence-v1.json').read_bytes())
    assert sidecar.deployment == d
    # A new pointer must not change historical exact-run evidence.
    lc.LifecycleStore(paths.state).ensure(ProfileStorage(paths.state/'profile.sqlite'),123,IDENTITY,'unused',manual=True)
    assert lc.RunDeploymentEvidence.model_validate_json((dest/'latent-deployment-evidence-v1.json').read_bytes()) == sidecar


def test_readiness_still_precedes_per_user_lifecycle(pipeline, monkeypatch):
    paths,effective,_,_=pipeline
    monkeypatch.setattr(lc,'load_runtime',lambda *a: pytest.fail('readiness first'))
    r=runner.run(integrated_args(latent_lifecycle='per-user-v1'), paths,effective,snapshot_loader=lambda _:None)
    assert (r.status,r.reason)==('not_ready','INTEREST_NOT_READY')
