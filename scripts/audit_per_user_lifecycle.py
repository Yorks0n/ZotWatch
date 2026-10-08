"""Read-only bounded staging audit of a restored real per-user generation.

Uses the persisted user's inputs and exact generic encoder. All fault injection
and local rebuilds occur in a new isolated state; it never changes Zotero,
confirmed interests, a user caller, or remote state.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.models import ZoteroItem
from zotwatch.interests.lifecycle import LifecycleStore, FORMATION_POLICY, load_runtime
from zotwatch.interests.lifecycle_transport import pack, unpack
from zotwatch.interests.latent import rebuild_reason


def audit(source, output, cache, repository_id):
    source,output=map(Path,(source,output))
    if output.exists():raise ValueError('Audit requires a new private output directory')
    output.mkdir(parents=True,mode=0o700)
    initial=LifecycleStore(source);d=initial.current()
    g,_,_=initial.load(d,repository_id,d.library_identity_sha256)
    restore=output/'independent-run'
    unpack(pack(initial,repository_id),restore,repository_id)
    store=LifecycleStore(restore)
    now=datetime.fromisoformat(g.built_at)
    gate=SimpleNamespace(**g.model_dump(),parameters=FORMATION_POLICY)
    def reason(hashes,days):
        return rebuild_reason(gate,hashes,g.library_identity_sha256,g.embedding_fingerprint,
            FORMATION_POLICY,g.clustering_runtime,now+timedelta(days=days))
    hashes=dict(g.item_text_hashes)
    checks={
        'monthly_before':reason(hashes,29)=='reuse_before_monthly_check',
        'monthly_unchanged':reason(hashes,31)=='reuse_no_material_change',
        'monthly_added_30':reason({**hashes,**{f'audit-new-{i}':'0'*64 for i in range(30)}},31)=='material_library_change',
        'monthly_edited_5_percent':reason({k:('0'*64 if i<max(1,int(len(hashes)*.05)+1) else v) for i,(k,v) in enumerate(hashes.items())},31)=='material_library_change',
    }
    for name,rid,identity in [('wrong_repository',repository_id+1,d.library_identity_sha256),('wrong_library',repository_id,'f'*64)]:
        try:store.load(d,rid,identity)
        except ValueError:checks[name]=True
        else:checks[name]=False
    _,runtime=load_runtime(store,repository_id,d.library_identity_sha256,cache)
    checks['real_encoder_load']=runtime.model.interest_model_revision==d.interest_model_revision
    model_path=store.root/'generations'/d.interest_model_revision/'model.json'
    before=model_path.read_bytes()
    model_path.write_bytes(b'{}')
    try:load_runtime(store,repository_id,d.library_identity_sha256,cache)
    except ValueError:checks['corrupt_model_rejected']=True
    else:checks['corrupt_model_rejected']=False
    model_path.write_bytes(before)
    items=tuple(ZoteroItem(key=r.key,version=g.library_revision,title=r.title,abstract=r.abstract,doi=r.doi,raw={'data':{'itemType':'journalArticle'}}) for r in g.input_records)
    snapshot=SimpleNamespace(library_identity_sha256=g.library_identity_sha256,revision=g.library_revision,items=items)
    storage=SimpleNamespace(read_profile_snapshot=lambda:snapshot)
    # Failure must leave original current/generation available, even when the
    # loaded runtime differs from the builder's recorded dependency versions.
    token=store.token()
    def failed_encoder(*args):raise ValueError('Injected encoder failure')
    # Change one item's text to force the injected encoding boundary.
    changed=items[0].model_copy(update={'abstract':(items[0].abstract or '')+' audit'})
    altered=SimpleNamespace(**{**snapshot.__dict__,'items':(changed,*items[1:])})
    try:store.ensure(SimpleNamespace(read_profile_snapshot=lambda:altered),repository_id,d.library_identity_sha256,cache,manual=True,encode=failed_encoder)
    except ValueError:checks['failed_build_keeps_current']=store.token()==token
    else:checks['failed_build_keeps_current']=False
    rebuilt,why,encoded=store.ensure(storage,repository_id,d.library_identity_sha256,cache,manual=True)
    checks['successful_build_atomic_switch']=store.current()==rebuilt and rebuilt.interest_model_revision!=d.interest_model_revision
    checks['prior_generation_retained']=bool(store.load(d,repository_id,d.library_identity_sha256))
    newg,_,_=store.load(rebuilt,repository_id,d.library_identity_sha256)
    checks['stable_ids_preserved']=[c.interest_id for c in newg.centers]==[c.interest_id for c in g.centers]
    rollback=store.rollback(d.deployment_revision,repository_id,d.library_identity_sha256,store.token(),cache)
    checks['same_user_rollback']=rollback.interest_model_revision==d.interest_model_revision and rollback.activation_reason=='rollback'
    evidence={'schema_name':'zotwatch-p5c4-staging-audit','schema_version':1,'scope':'real-user-snapshot/local-isolated-fault-injection',
        'repository_id':repository_id,'initial':d.model_dump(),'rebuilt':rebuilt.model_dump(),'rollback':rollback.model_dump(),
        'input_count':len(g.input_records),'eligible_count':len(g.embedding_keys),'formal_centers':len(g.centers),
        'rebuild_reason':why,'encoded_items':encoded,'checks':checks}
    (output/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    if not all(checks.values()):raise ValueError('Bounded staging audit failed')
    return {k:evidence[k] for k in ('scope','input_count','eligible_count','formal_centers','encoded_items','checks')}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['source','output','cache']:p.add_argument('--'+name,required=True)
    p.add_argument('--repository-id',required=True,type=int)
    a=p.parse_args();print(json.dumps(audit(a.source,a.output,a.cache,a.repository_id)))
