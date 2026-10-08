"""Deep breadth, reusable sources and cancellation regressions (offline)."""
import json
import pytest
from app import db
from app.stage3 import agent, tools, report
from tests.test_agent import data_dir, _church, _profile

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(report, "complete_json", lambda *a, **k: {"at_a_glance": "Offline report"})
    monkeypatch.setattr("app.qa.answer", lambda *a, **k: {"answer": "Unknown", "sources": [], "confident": False})

def test_no_preferences_still_researches_and_reports_gaps(data_dir, monkeypatch):
    calls=[]
    def chat(*a, **k):
        calls.append(a)
        return {"tool_calls": [{"id": str(len(calls)), "function": {"name": "finish", "arguments": json.dumps({"summary": "Sources unavailable", "why": "No additional sources"})}}]}
    monkeypatch.setattr(agent, "chat_tools", chat)
    result=agent.deep_search(_church(), _profile(), "breadth")
    assert len(calls)==2
    assert result.open
    saved=json.loads(db.get_job("breadth")["result_json"])
    assert saved["limitations"] and len(saved["coverage"])==7

def test_page_persisted_calendar_not_fetched(data_dir, monkeypatch):
    ctx=tools.make_ctx(session_id="s",church_id="c",job_id="missing",profile=_profile(),church=_church())
    calls=[]
    monkeypatch.setattr(tools,"fetch",lambda url, **kw: calls.append(url) or {"url":url,"status":200,"text":"Public staff: Alex, pastor", "links":[]})
    tools.fetch_page(ctx,"https://example.org/staff")
    tools.fetch_page(ctx,"https://example.org/calendar")
    assert len(calls)==1
    assert db.research_sources("c",scope="deep")[0]["text"].startswith("Public staff")
    assert ctx["resources"][0]["kind"]=="calendar"

def test_cancelled_does_not_collect_sources(data_dir,monkeypatch):
    ctx=tools.make_ctx(session_id="s",church_id="c",job_id="missing",profile=_profile(),church=_church())
    monkeypatch.setattr(tools,"cancelled",lambda ctx: True)
    monkeypatch.setattr(tools,"fetch",lambda *a: pytest.fail("cancelled work fetched"))
    assert tools.fetch_page(ctx,"https://example.org/staff")["cancelled"]
    assert not db.research_sources("c")

def test_sermon_corpus_survives_new_context(data_dir,monkeypatch):
    ctx=tools.make_ctx(session_id="s",church_id="c",job_id="missing",profile=_profile(),church=_church())
    ctx["sermon_items"]={"s1":{"title":"Grace", "date":"2026-09-20", "speaker":"Alex", "page_url":"https://example.org/sermons/grace"}}
    monkeypatch.setattr(tools.sermons,"transcribe_sermon",lambda *a,**k:{"text":"We teach grace and forgiveness.","source":"transcript","minutes":20})
    tools.transcribe_sermons(ctx,["s1"])
    source=db.research_sources("c",scope="deep")[0]
    assert source["kind"]=="sermon" and source["speaker"]=="Alex"
    assert source["text"]=="We teach grace and forgiveness."

def test_supported_coverage_requires_fetched_sources(data_dir):
    ctx=tools.make_ctx(session_id="s",church_id="c",job_id="missing",profile=_profile(),church=_church())
    assert "error" in tools.review_coverage(ctx,"public_staff","supported","Found staff",["https://example.org/staff"])

def test_feed_reuses_saved_sermon_without_transcription(data_dir,monkeypatch):
    ctx=tools.make_ctx(session_id="s",church_id="c",job_id="missing",profile=_profile(),church=_church())
    url="https://example.org/sermon/grace"
    ctx["saved_sources"]=[{"url":url,"kind":"sermon","text":"Grace teaching", "title":"Grace"}]
    monkeypatch.setattr(tools.sermons,"get_sermons",lambda *a:{"items":[{"page_url":url,"title":"Grace","audio_url":"https://example.org/a.mp3"}]})
    monkeypatch.setattr(tools.sermons,"transcribe_sermon",lambda *a,**k:pytest.fail("Repeated transcription"))
    listing=tools.get_sermons(ctx,"https://example.org/feed")
    tools.transcribe_sermons(ctx,[listing["sermons"][0]["sermon_id"]])
    assert ctx["transcripts"]["s1"]["text"]=="Grace teaching"

def test_fresh_shared_coverage_rebuilds_current_report(data_dir,monkeypatch):
    from datetime import datetime, timezone
    original="2026-10-07T00:00:00+00:00"
    # Use current time while retaining the exact previous verification value.
    original=datetime.now(timezone.utc).isoformat()
    db.research_put("c-deep","https://example.org/staff","Public staff: Alex",scope="deep")
    db.create_job("old","another-person","c-deep")
    coverage={area:{"status":"supported","summary":"Checked","urls":["https://example.org/staff"]} for area in tools.COVERAGE_AREAS}
    db.update_job("old",kind="deep",status="complete",finished_at=original,verified_at=original,result_json=json.dumps({"coverage":coverage,"resources":[],"limitations":[],"sermons_analysed":8}))
    monkeypatch.setattr(agent,"chat_tools",lambda *a,**k:pytest.fail("Fresh complete coverage re-researched"))
    result=agent.deep_search(_church(),_profile(session_id="current-person"),"new")
    assert result.profile_session=="current-person"
    assert result.sermons_analysed==8
    assert db.get_job("new")["verified_at"]==original

def test_old_source_verified_at_request_time(data_dir,monkeypatch):
    from datetime import datetime, timezone, timedelta
    stale=(datetime.now(timezone.utc)-timedelta(days=45)).isoformat()
    url="https://example.org/staff"
    db.research_put("c-deep",url,"Old staff",scope="deep",checked_at=stale)
    calls=[]
    monkeypatch.setattr(tools,"fetch",lambda url,**kw:calls.append((url,kw)) or {"url":url,"text":"New staff","status":200,"links":[],"checked_at":datetime.now(timezone.utc).isoformat()})
    monkeypatch.setattr(agent,"chat_tools",lambda *a,**k:{"tool_calls":[{"id":"finish","function":{"name":"finish","arguments":json.dumps({"summary":"Partial","why":"No other sources"})}}]})
    agent.deep_search(_church(),_profile(),"old-source")
    assert calls[0][1]["max_age_days"]==30
    assert db.research_sources("c-deep",scope="deep")[0]["text"]=="New staff"

def test_exact_quotes_allow_whitespace_but_reject_paraphrase():
    assert tools.quote_verbatim("Sunday worship begins at ten.","Sunday  worship\nbegins at ten.")
    assert not tools.quote_verbatim("Sunday worship starts at ten.","Sunday worship begins at ten.")

def test_evidence_cannot_borrow_other_url(data_dir):
    ctx=tools.make_ctx(session_id="s",church_id="c",job_id="missing",profile=_profile(),church=_church())
    tools._track_fetched(ctx,"https://example.org/beliefs","We believe marriage is between one man and one woman.")
    evidence={"feature":"lgbtq.marriage","value":"traditional","tier":"A","quote":"We believe marriage is between one man and one woman.","url":"https://another.org/beliefs","source_kind":"statement_of_faith","how":"stated"}
    assert not tools.record_evidence(ctx,[evidence])["ok"]

def test_full_staff_requires_inspecting_long_page(data_dir):
    ctx=tools.make_ctx(session_id="s",church_id="c",job_id="missing",profile=_profile(),church=_church())
    url="https://example.org/staff"
    text="Alex, pastor. "*1500+"Jordan, ministry director."
    tools._track_fetched(ctx,url,text)
    first=tools.read_source(ctx,url)
    rejected=tools.review_coverage(ctx,"public_staff","supported","All staff",[url])
    assert "error" in rejected
    chunk=tools.read_source(ctx,url,offset=first["next_offset"])
    assert "Jordan" in chunk["text"]
    assert tools.review_coverage(ctx,"public_staff","supported","All staff",[url])["ok"]

def test_sermon_analysis_cancelled_between_model_calls(data_dir,monkeypatch):
    from app.stage3 import sermons
    calls=[]
    stop={"value":False}
    def analyse(text, metadata):
        calls.append(text)
        stop["value"]=True
        return {"topics":["grace"]}
    monkeypatch.setattr(sermons,"_analyse_one_sermon",analyse)
    result=sermons.analyse_sermons([{"text":"First sermon"},{"text":"Second sermon"}],[],cancelled=lambda:stop["value"])
    assert calls==["First sermon"]
    assert result["evidence"]==[]
