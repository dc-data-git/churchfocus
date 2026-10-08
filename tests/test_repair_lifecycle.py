"""Repair acceptance: authorization, stale work, truthful actions, memory and reuse. Offline."""
import json, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import pytest
from app import db,jobs,memory,chat
from app.models import Church,DenomGuess,MemoryOp,Evidence
from app.stage1 import search
from app.stage0 import conversation

class Pool:
    def __init__(self): self.calls=[]
    def submit(self,fn,*args): self.calls.append((fn,args))

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv("DATA_DIR",str(tmp_path))
    from app.config import get_settings
    get_settings.cache_clear()
    db.init();search._mem.clear();jobs._cancelled.clear()
    pools={kind:Pool() for kind in ("medium","deep")}
    monkeypatch.setattr(jobs,"_pools",pools)
    monkeypatch.setattr(search,"request_coverage",lambda *args:None)
    for n in range(6):
        cid=f"c{n}"
        db.session_church_put("s",cid,{"church_id":cid,"name":"First Presbyterian Church" if n==0 else f"Grace Church {n}","lat":38.1,"lng":-97.4,"website":f"https://c{n}.example.org","distance_miles":n+1},
                              DenomGuess(label="Unknown",method="unknown").model_dump(mode="json"))
    monkeypatch.setattr(conversation,"_crisis",lambda text:False)
    monkeypatch.setattr("app.llm.complete_json",lambda task,*args,**kw:{"reply":"I have started the deep dive","intent":"chat"} if task=="interview_turn" else {})
    yield pools
    get_settings.cache_clear()

def result():
    return json.dumps({"research_version":5,"facts":[{"label":"Service times","value":"Sunday 10:45"}],"staff":[],"pages":[]})

def test_no_medium_from_discovery_or_table(env,monkeypatch):
    conversation._first_search("s",38.1,-97.4,15,"Hesston")
    assert search.table("s")["total"]==6
    assert db.jobs_for_session("s")==[]

def test_prepare_hides_finished_results_until_submit(env):
    jobs.prepare("s")
    pending=db.jobs_for_session("s")
    assert len(pending)==5 and all(not j["visible"] for j in pending)
    for job in pending:
        db.update_job(job["job_id"],status="complete",result_json=result(),finished_at=datetime.now(timezone.utc).isoformat())
    assert jobs.status("s")==[]
    assert all(row["summary"] is None for row in search.table("s")["rows"])
    conversation.know_more("s",[pending[1]["church_id"]])
    rows=search.table("s")["rows"]
    assert rows[0]["pinned"] and rows[0]["summary"]
    assert len(jobs.status("s"))==1
    assert len(chat.since("s"))==1 and "What would you like to ask" in chat.since("s")[0]["text"]

def test_submit_cancels_unselected_pending_keeps_independent_pin(env):
    jobs.pin("s","c5",True)
    jobs.prepare("s")
    jobs.select("s",["c0"])
    state=db.session_state("s")
    assert state["pins"]==["c5","c0"] and not state["selection_mode"]
    assert all(j["status"]=="cancelled" for j in db.jobs_for_session("s") if j["church_id"] not in state["pins"])

def test_unpin_cancels_and_deduplicates(env):
    jobs.pin("s","c0",True);jobs.pin("s","c0",True)
    assert len(db.jobs_for_session("s"))==1
    job=db.jobs_for_session("s")[0]
    jobs.pin("s","c0",False)
    assert jobs.is_cancelled(job["job_id"])
    db.update_job(job["job_id"],status="complete",result_json=result())
    assert db.get_job(job["job_id"])["status"]=="cancelled"

def test_concurrent_submission_is_atomic(env):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids=list(pool.map(lambda _:jobs.submit("s","c0","deep"),range(16)))
    assert len(set(ids))==1 and len(env["deep"].calls)==1

def test_reset_stops_old_job_and_hides_terminal_old_result(env):
    jid=jobs.submit("s","c0","deep")
    search.reset("s")
    assert jobs.is_cancelled(jid) and jobs.status("s")==[]
    db.update_job(jid,status="error")
    assert db.get_job(jid)["status"]=="cancelled"

def test_old_discovery_cannot_repopulate_new_search(env,monkeypatch):
    started=threading.Event();release=threading.Event()
    def places(*args,**kw):
        started.set();release.wait(3)
        return [{"church_id":"old","name":"Old Church","lat":38.1,"lng":-97.4}]
    monkeypatch.setattr("app.stage1.places.search_churches",places)
    worker=threading.Thread(target=search.ensure_coverage,args=("s",38.1,-97.4,10))
    worker.start();assert started.wait(2)
    search.reset("s");release.set();worker.join(3)
    assert not worker.is_alive() and db.session_churches("s")==[] and db.coverage_get("s")==[]

def test_failed_map_search_does_not_claim_empty_success(env,monkeypatch):
    def fail(*a,**k): raise RuntimeError("provider unavailable")
    monkeypatch.setattr("app.stage1.places.search_churches",fail)
    monkeypatch.setattr("app.stage1.osm.search_churches_osm",fail)
    with pytest.raises(RuntimeError):search.ensure_coverage("s",38.1,-97.4,10)
    assert db.coverage_get("s")==[]

def test_refresh_boundaries_preserve_verification_time(env):
    now=datetime.now(timezone.utc)
    db.create_job("old","elsewhere","c0")
    original=(now-timedelta(days=6)).isoformat()
    db.update_job("old",kind="medium",status="complete",finished_at=original,verified_at=original,result_json=result())
    jid=jobs.submit("s","c0","medium")
    assert db.get_job(jid)["status"]=="complete" and db.get_job(jid)["verified_at"]==original
    db.update_job("old",verified_at=(now-timedelta(days=8)).isoformat())
    db.update_job(jid,verified_at=(now-timedelta(days=8)).isoformat())
    fresh=jobs.submit("s","c0","medium")
    assert db.get_job(fresh)["status"]=="pending" and env["medium"].calls

def test_research_retention_independent_scopes(env):
    now=datetime.now(timezone.utc)
    db.research_put("c0","https://c0.example.org/staff","Pastor Jane",checked_at=(now-timedelta(days=91)).isoformat())
    db.research_put("c0","https://c0.example.org/sermon","Love your neighbor",scope="deep",kind="sermon",checked_at=(now-timedelta(days=91)).isoformat())
    assert [r["kind"] for r in db.research_sources("c0")]==["sermon"]

def test_question_dedup_normalized(env):
    assert db.question_add("s","c0","When is worship?")==db.question_add("s","c0"," when IS worship! ")
    assert len(db.questions_list("s"))==1

def test_history_and_neutral_survive_current_revisions(env):
    memory.append("s",[MemoryOp(t=1,key="worship.style",val="traditional_hymns",ev="We love hymns",why="explicit")])
    memory.append("s",[MemoryOp(t=2,op="revise",key="worship.style",val="traditional_hymns",stance="neutral",ev="Doesn't matter now",why="correction",supersedes=1)])
    context=memory.context("s")
    assert len(context["history"])==2 and context["current"]["worship.style"]["stance"]=="neutral"
    assert not memory.to_profile("s").preferences
    assert "correction" in conversation._system_prompt("s","Hello")

def test_alias_deep_request_really_launches(env):
    conversation.turn("s","Do a deep dive on First Pres")
    assert len(env["deep"].calls)==1 and db.jobs_for_session("s")[0]["church_id"]=="c0"
    assert "progress stays visible" in chat.since("s")[-1]["text"]

def test_ambiguous_church_does_not_launch_or_promise(env):
    conversation.turn("s","Do a deep dive on Grace Church")
    assert not env["deep"].calls
    assert "Which church" in chat.since("s")[-1]["text"]

def test_same_geographic_place_correction_keeps_pins(env,monkeypatch):
    memory.append("s",[MemoryOp(t=1,key="location",val={"text":"Hesston","lat":38.13,"lng":-97.43,"limit_miles":20})])
    db.state_update("s",pins=["c0"])
    monkeypatch.setattr("app.stage1.places.geocode",lambda _: (38.13,-97.43))
    conversation._set_location("s",2,{"text":"Hesston, Kansas"},"Hesston Kansas")
    assert db.session_state("s")["generation"]==0 and db.session_state("s")["pins"]==["c0"]

def test_new_search_offers_choice_before_reset(env):
    jid=jobs.submit("s","c0","deep")
    conversation.turn("s","Start over again")
    assert db.get_job(jid)["status"]=="pending"
    assert chat.since("s")[-1]["meta"]["options"]==["Start a new chat","Change this search here"]
    conversation.turn("s","Change this search here")
    assert jobs.is_cancelled(jid) and db.session_state("s")["generation"]==1

def test_radius_endpoint_updates_current_memory(env):
    from fastapi.testclient import TestClient
    from app.main import app
    memory.append("s",[MemoryOp(t=1,key="location",val={"text":"Hesston","lat":38.13,"lng":-97.43,"limit_miles":15})])
    client=TestClient(app)
    assert client.post("/api/radius",json={"session_id":"s","radius":30}).status_code==200
    assert memory.to_profile("s").max_miles==30
    assert client.get("/api/state/s").json()["location"]["limit_miles"]==30

def test_invalid_selection_is_rejected_atomically(env):
    from fastapi.testclient import TestClient
    from app.main import app
    client=TestClient(app)
    assert client.post("/api/know_more",json={"session_id":"s","church_ids":[]}).status_code==422
    assert client.post("/api/know_more",json={"session_id":"s","church_ids":["c0","missing"]}).status_code==422
    assert not db.session_state("s")["pins"] and not db.jobs_for_session("s")


def test_legacy_partial_medium_results_are_refreshed(env):
    db.create_job("old-scoped","s","c0")
    db.update_job("old-scoped",kind="medium",status="complete",result_json=json.dumps({"pages":[]}),finished_at=datetime.now(timezone.utc).isoformat())
    jid=jobs.submit("s","c0","medium")
    assert db.get_job(jid)["status"]=="pending" and len(env["medium"].calls)==1

def test_continue_api_honors_pending_location(env,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    db.state_update("s",pending_location={"text":"Wichita, Kansas","limit_miles":30},restart_pending=True)
    monkeypatch.setattr("app.stage1.places.geocode",lambda text:(37.68,-97.33))
    monkeypatch.setattr(conversation,"_first_search",lambda *a:None)
    response=TestClient(app).post("/api/restart",json={"session_id":"s","mode":"continue"})
    assert response.status_code==200 and memory.location("s")["text"]=="Wichita, Kansas"
    assert "updated the starting place" in chat.since("s")[-1]["text"]

def test_supplied_public_url_adds_and_starts_medium(env,monkeypatch):
    monkeypatch.setattr("app.web.fetch",lambda url,**kw:{"url":url,"status":200,"text":"Public Church Sunday worship 10:30","title":"Public Church"})
    monkeypatch.setattr(search,"_fast_denom",lambda c:DenomGuess(label="Unknown",method="unknown"))
    conversation.turn("s","Look at https://public.example.org/")
    assert len(env["medium"].calls)==1 and len(db.session_state("s")["pins"])==1

def test_supplied_url_failure_does_not_promise_research(env,monkeypatch):
    monkeypatch.setattr("app.web.fetch",lambda url,**kw:{"url":url,"status":403,"text":""})
    conversation.turn("s","Look at https://public.example.org/")
    assert not env["medium"].calls and "couldn't open" in chat.since("s")[-1]["text"]

def test_medium_reuse_clock_does_not_reset_via_cache_copy(env):
    original=(datetime.now(timezone.utc)-timedelta(days=6)).isoformat()
    db.create_job("prior","prior-session","c0")
    db.update_job("prior",kind="medium",status="complete",result_json=result(),finished_at=original,verified_at=original)
    first=jobs.submit("s","c0","medium")
    second=jobs.submit("s","c0","medium")
    assert db.get_job(first)["verified_at"]==original==db.get_job(second)["verified_at"]
