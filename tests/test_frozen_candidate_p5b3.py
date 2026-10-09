from copy import deepcopy
import json

import numpy as np
import pytest

from zotwatch.interests.frozen_candidate import POLICY, canonical, digest, file_digest, load_frozen, encode_candidates
from .test_dual_gate_benchmark_p5b4 import snapshot, unit


def capsule(tmp_path):
    x=unit([[1,0]]*12)
    inputs={"keys":[f"K{i:03}" for i in range(12)],"titles":[f"work {i}" for i in range(12)],
        "abstracts":["abstract"]*12,"dois":[None]*12}
    state=snapshot(x); state.update(input_digest=digest(inputs),encoder_artifacts={})
    tmp_path.mkdir(exist_ok=True)
    (tmp_path/'inputs.json').write_bytes(canonical(inputs))
    (tmp_path/'snapshot.json').write_bytes(canonical(state))
    np.save(tmp_path/'embeddings.npy',x)
    manifest={"policy":deepcopy(POLICY),"model_revision":state['model_revision'],"exact_snapshot_sha256":digest(state),
        "files":{name:file_digest(tmp_path/name) for name in ('inputs.json','snapshot.json','embeddings.npy')}}
    (tmp_path/'manifest.json').write_bytes(canonical(manifest))
    return state,x


def test_private_capsule_roundtrip_and_checksum_corruption(tmp_path):
    state,x=capsule(tmp_path)
    restored,_,vectors=load_frozen(tmp_path)
    assert restored==state and np.array_equal(x,vectors)
    with (tmp_path/'snapshot.json').open('ab') as out: out.write(b' ')
    with pytest.raises(ValueError,match='checksum'): load_frozen(tmp_path)


def test_reject_policy_and_path_drift(tmp_path):
    capsule(tmp_path)
    p=tmp_path/'manifest.json';m=json.loads(p.read_text());m['policy']['minimum_margin']=.01
    p.write_bytes(canonical(m))
    with pytest.raises(ValueError,match='policy'): load_frozen(tmp_path)
    m['policy']=deepcopy(POLICY);m['files']['../outside']=m['files']['snapshot.json'];p.write_bytes(canonical(m))
    with pytest.raises(ValueError,match='checksum'): load_frozen(tmp_path)


def test_reject_title_only_before_model_loading(tmp_path):
    capsule(tmp_path)
    with pytest.raises(ValueError,match='insufficient_semantic_text'):
        encode_candidates(tmp_path,'no-cache',[{'title':'only title','abstract':None}])


def test_no_empty_or_extra_manifest_file_set(tmp_path):
    capsule(tmp_path)
    p=tmp_path/'manifest.json';m=json.loads(p.read_text());m['files'].pop('inputs.json');p.write_bytes(canonical(m))
    with pytest.raises(ValueError,match='Incomplete'):load_frozen(tmp_path)
