"""v2 conversation, jobs and Q&A (REDESIGN §6-§8). No network."""
from __future__ import annotations

import json
import threading
import time

import pytest

from app import chat, db, jobs, memory, qa
from app.models import Church, DenomGuess, Evidence, MemoryOp
from app.stage0 import conversation
from tests.fakes import FakeLLM

CH = Church(church_id="c1", name="Bethel Church", address="Hesston, KS", lat=38.1, lng=-97.4, website="https://bethel.example.org",
            distance_miles=2.0, denomination=DenomGuess(label="Mennonite Church USA", confidence=0.9, method="name"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings
    get_settings.cache_clear()
    db.init()
    memory._versions.clear()
    memory._table_versions.clear()
    jobs._cancelled.clear()
    from app.stage1 import search
    monkeypatch.setattr(search, "get_church", lambda sid, cid: CH.model_copy(update={"evidence": db.get_evidence(cid)}) if cid == "c1" else None)
    monkeypatch.setattr(search, "churches", lambda sid: [CH])
    monkeypatch.setattr(search, "ranked", lambda sid: [])
    fake = FakeLLM(responses={"crisis_check": {"escalate": False, "kind": "none", "reason": ""}})
    monkeypatch.setattr("app.llm.complete_json", fake.complete_json)
    yield fake
    get_settings.cache_clear()


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_greeting_once(env):
    conversation.turn("s1", None)
    conversation.turn("s1", None)
    msgs = chat.since("s1", 0)
    assert len(msgs) == 1 and "Where are you starting from" in msgs[0]["text"]


def test_turn_applies_valid_ops_and_drops_bad(env):
    env.responses["interview_turn"] = {
        "reply": "Hymns it is.", "intent": "chat", "options": ["Show me the matches"],
        "memory_ops": [
            {"key": "worship.style", "val": "traditional_hymns", "stance": "want", "strength": 0.6, "conf": 0.8, "src": "stated", "ev": "we love hymns"},
            {"key": "made.up", "val": "x"},
            {"key": "worship.style", "val": "not_a_value"},
        ]}
    out = conversation.turn("s2", "We love hymns")
    cur = memory.current("s2")
    assert cur["worship.music_sources"].val == "hymns"
    assert cur["worship.music_sources"].t == 1
    assert [m["text"] for m in out["messages"]] == ["We love hymns", "Hymns it is."]
    assert out["messages"][-1]["meta"]["options"] == ["Show me the matches"]
    pref = memory.to_profile("s2").preferences[0]
    assert pref.feature == "worship.music_sources" and pref.weight == "important"


def test_location_geocodes_and_starts_search(env, monkeypatch):
    from app.stage1 import places
    monkeypatch.setattr(places, "geocode", lambda text: (38.13, -97.43))
    started = []
    monkeypatch.setattr(conversation, "_first_search", lambda *a: started.append(a))
    env.responses["interview_turn"] = {"reply": "Great — I've started looking.", "intent": "chat",
                                       "location": {"text": "Hesston, KS", "limit_miles": 20}, "memory_ops": []}
    conversation.turn("s3", "Hesston KS, up to 20 miles")
    _wait(lambda: started)
    loc = memory.location("s3")
    assert loc["lat"] == 38.13 and loc["limit_miles"] == 20
    assert started and started[0][3] == 20
    # same place again -> no new search
    conversation.turn("s3", "still Hesston")
    time.sleep(0.1)
    assert len(started) == 1


def test_crisis_message_replaces_reply(env):
    env.responses["interview_turn"] = {"reply": "ok", "memory_ops": [{"key": "worship.style", "val": "traditional_hymns"}]}
    out = conversation.turn("s4", "I want to kill myself")
    assert "988" in out["messages"][-1]["text"]
    assert memory.current("s4") == {}


def test_medium_job_completes_without_unsolicited_chat_dump(env, monkeypatch):
    memory.append("s5", [MemoryOp(t=1, key="worship.style", val="traditional_hymns", strength=0.7, conf=0.9, src="stated")])

    def fake_medium(church, profile, *, progress=None, cancelled=None, max_pages=5):
        progress(1, 2, "Reading Bethel Church's website — 1 of 2 pages")
        from app.match import score
        from app.denom.kb import get_kb
        return {"church": church, "match": score(church, profile, get_kb()), "settled": [], "open": ["worship.style"],
                "evidence": [], "pages": [{"url": "https://bethel.example.org/", "kind": "home"}],
                "deep_dive_candidate": "strong", "reason": "x"}
    monkeypatch.setattr("app.stage2.summary.medium_search", fake_medium)
    jid = jobs.submit("s5", "c1", "medium")
    assert _wait(lambda: db.get_job(jid)["status"] == "complete")
    assert not any(m["meta"].get("kind") == "church_summary" for m in chat.since("s5", 0))
    st = jobs.status("s5")[0]
    assert st["name"] == "Bethel Church" and st["pct"] == 100
    assert qa.open_questions("s5", "c1")       # open feature -> Open question
    # reuse within 30 days: immediately complete, no second run
    monkeypatch.setattr("app.stage2.summary.medium_search", lambda *a, **k: pytest.fail("should reuse"))
    jid2 = jobs.submit("s5b", "c1", "medium")
    assert db.get_job(jid2)["status"] == "complete"


def test_medium_job_cancel(env, monkeypatch):
    gate = threading.Event()

    def slow(church, profile, *, progress=None, cancelled=None, max_pages=5):
        gate.wait(3)
        return {"church": church, "cancelled": True} if cancelled() else pytest.fail("not cancelled")
    monkeypatch.setattr("app.stage2.summary.medium_search", slow)
    jid = jobs.submit("s6", "c1", "medium")
    _wait(lambda: db.get_job(jid)["status"] == "running")
    jobs.cancel(jid)
    gate.set()
    time.sleep(0.2)
    assert db.get_job(jid)["status"] == "cancelled" and jobs.is_cancelled(jid)


def test_know_more_cancels_other_medium_jobs(env, monkeypatch):
    other = "j-other"
    db.create_job(other, "s7", "c9")
    db.update_job(other, kind="medium", status="running")
    monkeypatch.setattr(jobs, "submit", lambda sid, cid, kind, **k: "new")
    conversation.know_more("s7", ["c1"])
    assert db.get_job(other)["status"] == "cancelled"


def test_qa_answers_only_with_verbatim_source(env):
    page = "Bethel Church sings from the hymnal every Sunday at 10:30, with a choir."
    db.cache_put("https://bethel.example.org/", json.dumps({"text": page}))
    db.add_evidence("c1", [Evidence(feature="worship.style", value="traditional_hymns", tier="A", quote=page,
                                    url="https://bethel.example.org/", source_kind="website", how="stated")])
    env.responses["church_qa"] = {"answer": "Yes, they sing hymns with a choir.", "confident": True,
                                  "sources": [{"url": "https://bethel.example.org/", "quote": "sings from the hymnal every Sunday at 10:30"}]}
    a = qa.answer("s8", "c1", "Do they sing hymns?")
    assert a["confident"] and a["sources"] and not qa.open_questions("s8")
    env.responses["church_qa"] = {"answer": "Yes, there is a nursery.", "confident": True,
                                  "sources": [{"url": "https://bethel.example.org/", "quote": "Our nursery is open for ages 0-3"}]}
    b = qa.answer("s8", "c1", "Is there a nursery?")
    assert not b["confident"] and "Open questions" in b["answer"]
    assert [q["text"] for q in qa.open_questions("s8")] == ["Is there a nursery?"]


def test_user_edit_remove(env):
    memory.append("s9", [MemoryOp(t=1, key="worship.style", val="traditional_hymns", src="stated")])
    conversation.apply_user_edit("s9", "worship.style", "")
    assert "worship.style" not in memory.current("s9")


def test_qa_rejects_quote_at_wrong_url(env):
    quote = "Services are Sunday at 10:30."
    db.add_evidence("c1", [Evidence(feature="logistics.service_times", value="Sunday 10:30", tier="A",
                                    quote=quote, url="https://bethel.example.org/", source_kind="website", how="stated")])
    env.responses["church_qa"] = {"answer": "Services are Sunday at 10:30.", "confident": True,
                                  "sources": [{"url":"https://unread.example.org/", "quote":quote}]}
    result = qa.answer("source-check", "c1", "When are services?")
    assert not result["confident"] and result["sources"] == []
    assert not result["answer"].startswith("Services are")


def test_questions_api_cannot_edit_another_session(env):
    from fastapi.testclient import TestClient
    from app.main import app
    qid = qa.add_question("owner", "c1", "When is worship?")
    response = TestClient(app).post("/api/questions/other", json={"id":qid,"status":"dropped"})
    assert response.status_code == 404
    assert qa.open_questions("owner")[0]["status"] == "open"


def test_reset_forgets_saved_candidates_and_coverage(env):
    from app.stage1 import search
    db.session_church_put("reset", "old", {"church_id":"old"}, {})
    db.coverage_add("reset", 38.1, -97.4, 50, 7)
    search.reset("reset")
    assert db.session_churches("reset") == [] and db.coverage_get("reset") == []


def test_v2_page_and_question_roundtrip(env):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert c.get("/").status_code == 200
    assert c.get("/static/app.js").status_code == 200
    greeting = c.post("/api/chat",json={"session_id":"route-v2","text":None})
    assert greeting.status_code == 200 and greeting.json()["messages"]
    added = c.post("/api/questions/route-v2",json={"church_id":"c1","text":"Is there a choir?"}).json()
    qid = added[0]["id"]
    changed = c.post("/api/questions/route-v2",json={"id":qid,"text":"Is there a nursery?"}).json()
    assert changed[0]["text"] == "Is there a nursery?"
    assert c.post("/api/questions/route-v2",json={"id":qid,"status":"dropped"}).json()[0]["status"] == "dropped"


def test_stage2_quote_url_is_the_page_that_was_read(env):
    from app.stage2.summary import _to_evidence
    quote = "Worship is Sunday at 10:30."
    item={"feature":"logistics.service_times","value":"Sunday 10:30","quote":quote,"status":"stated","url":"https://invented.example.org/"}
    page={"url":"https://bethel.example.org/","kind":"home","text":quote}
    ev=_to_evidence(item,page,quote)
    assert ev is not None and ev.url == page["url"]


def test_completed_deep_job_is_not_reused_for_new_questions(env,monkeypatch):
    from datetime import datetime,timezone
    db.create_job("old-deep", "old-session", "c1")
    db.update_job("old-deep",kind="deep",status="complete",finished_at=datetime.now(timezone.utc).isoformat())
    qa.add_question("fresh-question", "c1", "Do you have a nursery?")
    submitted=[]
    monkeypatch.setattr(jobs._pools["deep"],"submit",lambda fn,*args:submitted.append(fn))
    jid=jobs.submit("fresh-question","c1","deep")
    assert db.get_job(jid)["status"] == "pending" and submitted == [jobs._run_deep]


def test_live_model_add_synonym_preserves_preference(env):
    reasons = conversation._apply_ops("live-op",1,[{"op":"add","key":"worship.style","val":"traditional_hymns","strength":.7,"conf":.95,"src":"stated"}])
    assert reasons == []
    assert memory.to_profile("live-op").preferences[0].want == ["hymns"]


def test_location_memory_op_starts_search_when_top_level_field_missing(env,monkeypatch):
    monkeypatch.setattr("app.stage1.places.geocode",lambda text:(38.13,-97.43))
    monkeypatch.setattr(conversation,"_first_search",lambda *a:None)
    env.responses["interview_turn"]={"reply":"Looking nearby.","memory_ops":[{"key":"location","val":{"text":"Hesston, KS","limit_miles":10},"src":"stated"}]}
    conversation.turn("location-op","Hesston within 10 miles")
    assert memory.location("location-op")["lat"] == 38.13
    assert memory.to_profile("location-op").max_miles == 10


def test_chat_and_background_messages_allocate_unique_numbers(env):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers=list(pool.map(lambda n:chat.post("parallel-messages",str(n)),range(24)))
    assert sorted(numbers) == list(range(1,25))
    assert len(chat.since("parallel-messages",0)) == 24
