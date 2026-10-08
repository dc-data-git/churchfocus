"""Offline acceptance of broad research and source-grounded follow-ups."""
import json
import pytest
from app import db, qa, web
from app.models import Church, DenomGuess, PreferenceProfile
from app.stage2.website import site_pages
from app.stage2.summary import medium_search
from tests.fakes import FakeWeb, FakeLLM

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings
    get_settings.cache_clear()
    db.init()
    yield
    get_settings.cache_clear()
    web.set_client(None)

def church():
    return Church(church_id="c", name="Test Church", website="https://church.fixture/",
                  denomination=DenomGuess(label="Unknown", confidence=0, method="unknown"))

def test_crawl_recursive_nonkeyword_and_collection_boundary(store, monkeypatch):
    base="https://church.fixture"
    FakeWeb({base+"/":{"html":"<html><body><p>Welcome</p><a href='/x'>Explore</a><a href='/calendar'>Calendar</a></body></html>"},
             base+"/x":{"html":"<html><body><p>Useful church details</p><a href='/z'>Next</a></body></html>"},
             base+"/z":{"html":"<html><body><p>Sunday worship 10:45</p></body></html>"},
             base+"/calendar":{"html":"<html><body><p>Public event calendar</p><a href='/item'>Specific event</a></body></html>"},
             base+"/item":{"html":"<html><body><p>Do not scan this event</p></body></html>"}}).install(monkeypatch)
    resources, coverage=[], {}
    pages=site_pages(base, resources=resources, coverage=coverage)
    assert base+'/z' in {p['url'] for p in pages}
    assert base+'/item' not in {p['url'] for p in pages}
    assert any(r['kind']=='calendar' for r in resources)
    assert coverage['pages_scanned']==3 and not coverage['limited']

def test_medium_fullstaff_no_preferences_and_exact_sources(store, monkeypatch):
    text='Sunday worship at 10:45 am. Alice Smith Lead Pastor. Bob Jones Associate Pastor. An active small-group ministry meets weekly.'
    FakeWeb({'https://church.fixture/':{'html':'<html><body><p>'+text+'</p></body></html>'}}).install(monkeypatch)
    extraction={'features':[], 'facts':[{'label':'Service times','value':'10:45 am','quote':'Sunday worship at 10:45 am.'},
       {'label':'Active ministries','value':'Small groups','quote':'An active small-group ministry meets weekly.'},
       {'label':'Fake','value':'Invented','quote':'A nonexistent factual statement.'}],
       'staff':[{'name':'Alice Smith','position':'Lead Pastor','quote':'Alice Smith Lead Pastor.'},
       {'name':'Bob Jones','position':'Associate Pastor','quote':'Bob Jones Associate Pastor.'}]}
    fake=FakeLLM({'page_extract':extraction})
    monkeypatch.setattr('app.stage2.summary.complete_json',fake.complete_json)
    card=medium_search(church(), PreferenceProfile(session_id='s'))
    assert len(card['staff'])==2 and len(card['facts'])==2
    assert not card['coverage']['missing_basics']
    assert text in db.research_sources('c')[0]['text']
    assert 'women.senior_pastor' in fake.calls[0]['messages'][1]['content']

def test_cancellation_fetches_nothing_and_stores_nothing(store, monkeypatch):
    monkeypatch.setattr('app.stage2.website.fetch',lambda *a,**k: pytest.fail('cancelled crawl fetched'))
    card=medium_search(church(),PreferenceProfile(session_id='s'),cancelled=lambda:True)
    assert card['cancelled'] and db.research_sources('c')==[]

def test_retrieval_finds_late_sermon_text_after_restart(store):
    db.research_put('c','https://church.fixture/sermon','General words. '*3000+'We teach that baptism expresses faith.',kind='sermon',scope='deep')
    pages=qa._pages('c',question='What do the sermons teach about baptism?')
    assert 'baptism expresses faith' in pages[0]['text']

def test_targeted_calendar_lookup_no_deep_and_no_calendar_persistence(store, monkeypatch):
    db.create_job('j','s','c')
    db.update_job('j',kind='medium',status='complete',result_json=json.dumps({'resources':[{'kind':'calendar','url':'https://church.fixture/calendar','label':'Calendar'}]}))
    monkeypatch.setattr('app.stage1.search.get_church',lambda *a:church())
    FakeWeb({'https://church.fixture/calendar':{'html':'<html><body><p>The community supper is Friday at 6 pm.</p></body></html>'}}).install(monkeypatch)
    fake=FakeLLM({'church_qa':{'answer':'Community supper is Friday at 6 pm.','confident':True,'sources':[{'url':'https://church.fixture/calendar','quote':'The community supper is Friday at 6 pm.'}]}})
    monkeypatch.setattr('app.llm.complete_json',fake.complete_json)
    result=qa.answer('s','c','What upcoming events are there?')
    assert result['confident']
    assert db.research_sources('c')==[]
    assert len(db.jobs_for_session('s'))==1

def test_fuzzy_paraphrase_is_rejected(store, monkeypatch):
    db.research_put('c','https://church.fixture/','Sunday service is at 10:45 am.')
    monkeypatch.setattr('app.stage1.search.get_church',lambda *a:church())
    monkeypatch.setattr('app.qa._lookup',lambda *a:[])
    fake=FakeLLM({'church_qa':{'answer':'Service is 9 am.','confident':True,'sources':[{'url':'https://church.fixture/','quote':'Sunday service is at 9:00 am.'}]}})
    monkeypatch.setattr('app.llm.complete_json',fake.complete_json)
    assert not qa.answer('s','c','Service times?',record=False)['confident']

def test_link_text_stops_at_anchor_end():
    links=web._extract_links('<a href="/staff">Staff</a><p>Not part of label</p>','https://church.fixture/')
    assert links[0]['text']=='Staff'

def test_unchanged_refresh_reuses_extraction_and_source_date(store, monkeypatch):
    text='Alice Smith Lead Pastor. Sunday service is at 10 am.'
    FakeWeb({'https://church.fixture/':{'html':'<html><body><p>'+text+'</p></body></html>'}}).install(monkeypatch)
    fake=FakeLLM({'page_extract':{'features':[], 'facts':[{'label':'Service times','value':'10 am','quote':'Sunday service is at 10 am.'}],
          'staff':[{'name':'Alice Smith','position':'Lead Pastor','quote':'Alice Smith Lead Pastor.'}]}})
    monkeypatch.setattr('app.stage2.summary.complete_json',fake.complete_json)
    first=medium_search(church(),PreferenceProfile(session_id='s'))
    before=db.research_sources('c')[0]['checked_at']
    db.create_job('previous','s','c')
    db.update_job('previous',kind='medium',status='complete',result_json=json.dumps({'facts':first['facts'],'staff':first['staff'],'coverage':first['coverage']}))
    second=medium_search(church(),PreferenceProfile(session_id='s'))
    assert len(fake.calls)==1
    assert second['staff']==first['staff']
    assert db.research_sources('c')[0]['checked_at']==before

def test_medium_unreadable_site_is_failure(store, monkeypatch):
    FakeWeb({}).install(monkeypatch)
    with pytest.raises(RuntimeError, match='No permitted readable'):
        medium_search(church(),PreferenceProfile(session_id='s'))

def test_max_age_override_rechecks_old_cache(store, monkeypatch):
    from datetime import datetime, timezone, timedelta
    FakeWeb({'https://church.fixture/':{'html':'<html><body><p>New service time is 11 am.</p></body></html>'}}).install(monkeypatch)
    cached={'url':'https://church.fixture/','status':200,'text':'Old schedule','links':[]}
    monkeypatch.setattr(web,'cache_get',lambda key:(datetime.now(timezone.utc)-timedelta(days=8),json.dumps(cached)))
    page=web.fetch('https://church.fixture/',max_age_days=7)
    assert not page['from_cache'] and '11 am' in page['text']

def test_medium_reads_staff_beyond_first_page_chunk(store, monkeypatch):
    text='Ordinary factual content. '*900+'Alice Smith Lead Pastor.'
    FakeWeb({'https://church.fixture/':{'html':'<html><body><p>'+text+'</p></body></html>'}}).install(monkeypatch)
    def extract(task,messages,schema,**kwargs):
        quote='Alice Smith Lead Pastor.'
        return {'features':[], 'facts':[], 'staff':[{'name':'Alice Smith','position':'Lead Pastor','quote':quote}]
                if quote in messages[1]['content'] else []}
    monkeypatch.setattr('app.stage2.summary.complete_json',extract)
    card=medium_search(church(),PreferenceProfile(session_id='s'))
    assert card['staff'][0]['name']=='Alice Smith'

def test_expired_legacy_jobs_and_cachedpages_are_not_retrieved(store):
    from datetime import datetime, timezone, timedelta
    db.create_job('expired','s','c')
    old=(datetime.now(timezone.utc)-timedelta(days=91)).isoformat()
    db.update_job('expired',kind='medium',status='complete',finished_at=old,
                  result_json=json.dumps({'pages':[{'url':'https://church.fixture/'}],
                     'resources':[{'kind':'calendar','url':'https://church.fixture/calendar'}]}))
    db.cache_put('https://church.fixture/',json.dumps({'text':'Expired church claim.'}))
    assert qa._pages('c',question='Expired claim?')==[]

def test_expired_evidence_cannot_support_answer(store, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from app.models import Evidence
    db.add_evidence('c',[Evidence(feature='logistics.service_times',value='9 am',tier='A',quote='Sunday worship at 9 am.',
                    url='https://church.fixture/',source_kind='website',how='stated',
                    checked_at=datetime.now(timezone.utc)-timedelta(days=91))])
    monkeypatch.setattr('app.stage1.search.get_church',lambda *a:church())
    monkeypatch.setattr('app.qa._lookup',lambda *a:[])
    assert not qa.answer('s','c','Service time?',record=False)['confident']

def test_page_text_budget_reported(store, monkeypatch):
    monkeypatch.setattr('app.stage2.website.fetch', lambda *a,**k:{'url':'https://church.fixture/','status':200,
        'text':'Public text', 'links':[], 'text_truncated':True})
    coverage={}
    site_pages('https://church.fixture/',coverage=coverage)
    assert coverage['limited'] and coverage['truncated_pages']==['https://church.fixture/']

@pytest.mark.parametrize('url',['http://127.0.0.1/','http://localhost/','http://10.0.0.1/','http://169.254.169.254/',
    'http://[::1]/','http://user:password@example.com/','http://router.local/'])
def test_user_urls_reject_nonpublic_before_cache(url, monkeypatch):
    monkeypatch.setattr(web,'cache_get',lambda *a:pytest.fail('unsafe URL reached cache'))
    with pytest.raises(web.Blocked):
        web.fetch(url)

def test_redirect_to_private_is_blocked_before_request(store):
    import httpx
    requested=[]
    def handler(request):
        requested.append(str(request.url))
        if request.url.path=='/robots.txt':
            return httpx.Response(200,text='User-agent: *\nAllow: /')
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    web.set_client(httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=True))
    with pytest.raises(web.Blocked):
        web.fetch('https://church.fixture/')
    assert all('127.0.0.1' not in url for url in requested)

def test_dns_private_answer_is_blocked(monkeypatch):
    monkeypatch.setattr(web.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('10.0.0.4',443))])
    with pytest.raises(web.Blocked):
        web._validate_public_url('https://public.example/',resolve=True)

def test_larger_read_replaces_legacy_truncated_cache(store, monkeypatch):
    from datetime import datetime, timezone
    html='<html><body><p>'+'General content. '*1500+'Alice Smith Lead Pastor.</p></body></html>'
    FakeWeb({'https://church.fixture/':{'html':html}}).install(monkeypatch)
    payload={'url':'https://church.fixture/','status':200,'text':'x'*20000,'links':[]}
    monkeypatch.setattr(web,'cache_get',lambda *a:(datetime.now(timezone.utc),json.dumps(payload)))
    page=web.fetch('https://church.fixture/',max_chars=60000,max_age_days=7)
    assert not page['from_cache'] and 'Alice Smith Lead Pastor.' in page['text']

def test_redirect_alias_of_home_not_scanned_twice(store, monkeypatch):
    def fake(url,**kwargs):
        return {'url':'https://www.church.fixture/','status':200,'text':'Welcome to the church.',
                'links':[{'href':'http://church.fixture/','text':'Home'}]}
    monkeypatch.setattr('app.stage2.website.fetch',fake)
    pages=site_pages('https://church.fixture/')
    assert len(pages)==1

def test_medium_prompt_has_target_church_for_external_resource_attribution(store, monkeypatch):
    FakeWeb({'https://church.fixture/':{'html':'<html><body><p>Other Church worships at noon.</p></body></html>'}}).install(monkeypatch)
    fake=FakeLLM({'page_extract':{'features':[], 'facts':[], 'staff':[]}})
    monkeypatch.setattr('app.stage2.summary.complete_json',fake.complete_json)
    medium_search(church(),PreferenceProfile(session_id='s'))
    assert 'Target church: Test Church' in fake.calls[0]['messages'][1]['content']
    assert 'external churches' in fake.calls[0]['messages'][0]['content']

def test_hero_service_time_supplemented_when_article_extractor_omits_it(monkeypatch):
    html='<html><body><nav>Navigation menu</nav><header><h1>Sunday Worship Online</h1><p>Please join us for worship on Zoom at 9:00 am.</p></header><article><p>10:45 Sanctuary service</p></article><footer>Boilerplate footer</footer><script>Secret script</script></body></html>'
    monkeypatch.setattr(web.trafilatura,'extract',lambda *a,**k:'10:45 Sanctuary service')
    text=web._extract_text(html,'https://church.fixture/',60000)
    assert 'Zoom at 9:00 am' in text and '10:45 Sanctuary service' in text
    assert 'Boilerplate footer' not in text and 'Navigation menu' not in text and 'Secret script' not in text

def test_legacy_article_only_cache_refetched_after_parser_upgrade(store, monkeypatch):
    from datetime import datetime, timezone
    FakeWeb({'https://church.fixture/':{'html':'<html><body><p>Zoom worship at 9:00 am.</p></body></html>'}}).install(monkeypatch)
    old={'url':'https://church.fixture/','text':'10:45 Sanctuary service','status':200,'links':[]}
    monkeypatch.setattr(web,'cache_get',lambda *a:(datetime.now(timezone.utc),json.dumps(old)))
    page=web.fetch('https://church.fixture/',max_age_days=7)
    assert not page['from_cache'] and '9:00 am' in page['text'] and page['extract_version']==3

def test_reset_hides_obsolete_questions_without_deleting_history(store):
    from app.stage1 import search
    sid = "question-context-reset"
    search._mem.pop(sid, None)
    ch = church()
    db.session_church_put(sid, "c", ch.model_dump(mode="json"), ch.denomination.model_dump(mode="json"))
    qa.add_question(sid, "c", "What are service times?")
    assert len(qa.open_questions(sid)) == 1
    search.reset(sid)
    assert qa.open_questions(sid) == []
    assert db.questions_list(sid)[0]["text"] == "What are service times?"
    search._mem.pop(sid, None)
