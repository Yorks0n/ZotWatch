from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import pytest
from src.models import CandidateWork
from zotwatch.metadata import Cache, Providers, enrich, resolve, normalize_doi
from zotwatch.publication_dates import crossref_date, date_datetime, admission
from zotwatch.metadata_transport import MetadataGitState, BRANCH

NOW = datetime(2026,10,8,23,30,tzinfo=timezone.utc)
DOI = '10.1234/example'


def work(doi=DOI, abstract=None, source='crossref', **kw):
    return CandidateWork(source=source,identifier=doi or 'no-doi',doi=doi,title='Research paper',abstract=abstract,**kw)


def test_date_priority_precision_created_and_inclusive_utc_window():
    metadata={'created':{'date-time':'2026-10-08T15:00:00Z'},'published-online':{'date-parts':[[2026,10,1]]},'published':{'date-parts':[[2026,10,8]]}}
    row=work(published=date_datetime(metadata),extra=crossref_date(metadata))
    assert admission([row],NOW)[0]==[]
    metadata['published-online']['date-parts']=[[2026,10,2]]
    row.extra=crossref_date(metadata)
    assert admission([row],NOW)[0]==[row]
    assert row.extra['publication_source']=='crossref:published-online'
    assert crossref_date({'created':metadata['created']})['publication_precision']=='missing'
    metadata['published-online']['date-parts']=[[2026,10]]
    partial=crossref_date(metadata)
    assert partial['publication_date']=='2026-10' and partial['publication_precision']=='month'
    assert date_datetime(metadata) is None
    row.extra=partial
    assert list(admission([row],NOW)[1]['rejected'].values())==['publication_date_insufficient_precision']
    # Provider timestamps and exact dates land on the same UTC calendar date.
    timestamp=work(None,'abstract','arxiv',published=datetime.fromisoformat('2026-10-02T01:00:00+02:00'))
    assert admission([timestamp],NOW)[0]==[]
    timestamp.published=datetime.fromisoformat('2026-10-02T02:00:00+02:00')
    assert admission([timestamp],NOW)[0]==[timestamp]
    assert normalize_doi('https://doi.org/10.1234/EXAMPLE')==DOI


class Stub:
    def __init__(self, abstract='Europe PMC abstract', error=False):
        self.calls=[];self.abstract=abstract;self.error=error
    def crossref(self,doi):
        self.calls.append('crossref')
        return {'DOI':doi,'published-online':{'date-parts':[[2026,10,8]]}}
    def europe_pmc(self,doi):
        self.calls.append('europe_pmc')
        if self.error: raise OSError('service unavailable')
        return self.abstract
    def openalex(self,doi):
        self.calls.append('openalex');return None


def test_positive_cache_restored_separate_instance_and_existing_abstract_preserved(tmp_path):
    providers=Stub();cache=Cache(tmp_path,42);a=work()
    data=enrich([a],cache,NOW,providers)
    assert a.abstract=='Europe PMC abstract' and data['counts']['enriched_abstracts']==1
    assert providers.calls==['crossref','europe_pmc']
    providers.calls=[];b=work();c=work(abstract='Already valid original abstract')
    data=enrich([b,c],Cache(tmp_path,42),NOW+timedelta(days=1),providers)
    assert not providers.calls and data['counts']['persistent_cache_hits']==1
    assert b.abstract==a.abstract and c.abstract=='Already valid original abstract'
    assert c.extra['abstract_source']=='crossref'
    with pytest.raises(ValueError): Cache(tmp_path,99)


def test_negative_ttl_transient_failure_and_no_doi_do_not_use_title(tmp_path):
    providers=Stub(abstract=None);a=work();no_doi=work(None)
    enrich([a,no_doi],Cache(tmp_path,42),NOW,providers)
    assert a.abstract is None and no_doi.abstract is None
    assert a.extra['abstract_status']=='not_found'
    providers.calls=[]
    enrich([work()],Cache(tmp_path,42),NOW+timedelta(days=6),providers)
    assert providers.calls==[]
    enrich([work()],Cache(tmp_path,42),NOW+timedelta(days=8),providers)
    assert providers.calls==['crossref','europe_pmc','openalex']
    entry=resolve(DOI,NOW,Stub(error=True))
    assert entry['abstract_status']=='unavailable' and entry['expires_at']==(NOW+timedelta(hours=1)).isoformat()


def test_exact_doi_provider_matching(monkeypatch):
    providers=Providers()
    monkeypatch.setattr(providers,'get',lambda *a,**kw:{'message':{'DOI':'10.1234/wrong','abstract':'wrong'}})
    with pytest.raises(ValueError): providers.crossref(DOI)
    monkeypatch.setattr(providers,'get',lambda *a,**kw:{'resultList':{'result':[{'doi':'10.1234/wrong','abstractText':'wrong'},{'doi':DOI.upper(),'abstractText':'right'}]}})
    assert providers.europe_pmc(DOI)=='right'
    monkeypatch.setattr(providers,'get',lambda *a,**kw:{'doi':'https://doi.org/'+DOI,'abstract_inverted_index':{'Second':[1],'First':[0]}})
    assert providers.openalex(DOI)=='First Second'


def test_cache_transport_uses_independent_branch_and_restore_parent(tmp_path):
    cache=Cache(tmp_path,42);enrich([work()],cache,NOW,Stub())
    remote=object.__new__(MetadataGitState);remote.repository_id=42
    state={'head':None};calls=[]
    def api(method,path,body=None,missing=False):
        calls.append((method,path,body))
        if method=='GET' and '/git/ref/' in path: return {'object':{'sha':state['head']}} if state['head'] else None
        if path=='/git/blobs': return {'sha':'b'*40}
        if path=='/git/trees': return {'sha':'t'*40}
        if path=='/git/commits': return {'sha':'c'*40}
        if path=='/git/refs':state['head']=body['sha'];return {}
        raise AssertionError(path)
    remote.api=api
    remote.restore(tmp_path)
    result=remote.publish(tmp_path)
    assert result['commit_sha']=='c'*40
    assert any(body and body.get('ref')=='refs/heads/'+BRANCH for _,_,body in calls)
    assert not any('latent-state' in path or 'latent-state' in str(body) for _,path,body in calls)


def test_rss_public_enrichment_fallback_and_idempotency(monkeypatch):
    from .test_github_pages_rss import rss
    def request(url,*a,**kw):
        if 'api.crossref.org' in url:return json.dumps({'message':{'DOI':DOI}}).encode()
        if 'europepmc' in url:return json.dumps({'resultList':{'result':[{'doi':DOI,'abstractText':'<p>Enriched public abstract</p>'}]}}).encode()
        pytest.fail('OpenAlex should not be queried after successful enrichment')
    monkeypatch.setattr(rss,'request',request)
    assert rss.public_abstract('urn:doi:'+DOI)=='Enriched public abstract'
    result=dict(status='succeeded',command='watch',recommendations=[{'work_key':DOI,'title':'Paper'}])
    feed,n=rss.merge_feed(result,None,'https://example.com/feed.xml',NOW)
    assert n==1 and rss.merge_feed(result,feed,'https://example.com/feed.xml',NOW)==(feed,0)
