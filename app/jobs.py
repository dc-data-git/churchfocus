"""Durable authorized research jobs; speculative work never publishes before selection."""
from __future__ import annotations
import json, logging, threading, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Literal
from app import chat, db, memory

log = logging.getLogger("app.jobs")
_pools = {"medium": ThreadPoolExecutor(max_workers=5,thread_name_prefix="medium"),
          "deep": ThreadPoolExecutor(max_workers=2,thread_name_prefix="deep")}
_cancelled: set[str] = set()
_lock = threading.RLock()
REUSE_DAYS = {"medium":7,"deep":30}
KEEP_DAYS = {"medium":90,"deep":365}

def _now():
    return datetime.now(timezone.utc)

def _age_days(iso):
    try:
        value = datetime.fromisoformat(iso.replace("Z","+00:00"))
        return (_now()-value.replace(tzinfo=value.tzinfo or timezone.utc)).total_seconds()/86400
    except (ValueError,AttributeError):
        return 1e9

def _church(sid,cid):
    from app.stage1 import search
    church=search.get_church(sid,cid)
    if not church:
        raise KeyError(f"Unknown church: {cid}")
    return church

def submit(session_id: str, church_id: str, kind: Literal["medium","deep"], *, questions=(), visible=True) -> str:
    if kind not in _pools:
        raise ValueError("Unknown research kind")
    with _lock:
        church=_church(session_id,church_id)
        if kind=="deep" and questions:
            from app import qa
            for question in questions:
                if "?" in question or any(word in question.casefold() for word in ("service time","leadership","sermons about","beliefs about","ministries")):
                    qa.add_question(session_id,church_id,question)
        generation=db.session_state(session_id)["generation"]
        for job in reversed(db.jobs_for_session(session_id)):
            if (job["church_id"]==church_id and job["kind"]==kind and job["generation"]==generation
                    and job["status"] in ("pending","running")):
                if visible and not job["visible"]:
                    db.update_job(job["job_id"],visible=1)
                return job["job_id"]
        prev=db.latest_job(church_id,kind)
        job_id=uuid.uuid4().hex
        db.create_job(job_id,session_id,church_id)
        db.update_job(job_id,kind=kind,generation=generation,visible=int(visible),label=f"Waiting to start on {church.name}")
        # Medium facts are reusable. A deep report is rebuilt for this user's memory/questions.
        fresh=prev and _age_days(prev.get("verified_at") or prev.get("finished_at"))<REUSE_DAYS[kind]
        if fresh and kind=="medium":
            try:
                fresh=json.loads(prev.get("result_json") or "{}").get("research_version")==5
            except ValueError:
                fresh=False
        if fresh and kind=="medium" and prev.get("result_json"):
            db.update_job(job_id,status="complete",progress=100,done=1,total=1,
                          result_json=prev["result_json"],verified_at=prev.get("verified_at") or prev["finished_at"],
                          label=f"Saved research for {church.name}",finished_at=_now().isoformat())
            memory.bump_table(session_id)
            if visible:
                _seed_questions(session_id,church)
        else:
            _pools[kind].submit(_run_medium if kind=="medium" else _run_deep,session_id,church_id,job_id)
        return job_id

def cancel(job_id: str) -> None:
    with _lock:
        _cancelled.add(job_id)
        try:
            job=db.get_job(job_id)
        except KeyError:
            return
        if job["status"] in ("pending","running"):
            db.update_job(job_id,cancel=1,status="cancelled",label="Stopped",finished_at=_now().isoformat())

def is_cancelled(job_id: str) -> bool:
    if job_id in _cancelled:
        return True
    try:
        job=db.get_job(job_id)
        return bool(job["cancel"]) or job["generation"]!=db.session_state(job["session_id"])["generation"]
    except KeyError:
        return False

def reset_session(session_id: str) -> None:
    with _lock:
        db.state_reset(session_id)
        for job in db.jobs_for_session(session_id):
            if job["status"] in ("pending","running"):
                cancel(job["job_id"])
        memory.bump_table(session_id)

def prepare(session_id: str) -> dict:
    from app.stage1 import search
    with _lock:
        state=db.state_update(session_id,selection_mode=True)
        for church in search.top(session_id,5):
            submit(session_id,church.church_id,"medium",visible=church.church_id in state["pins"])
    return {"jobs":status(session_id),"research":db.session_state(session_id)}

def select(session_id: str, ids: list[str]) -> dict:
    ids=list(dict.fromkeys(ids))
    if not 1<=len(ids)<=5:
        raise ValueError("Select between 1 and 5 churches")
    for cid in ids:
        _church(session_id,cid)
    with _lock:
        state=db.session_state(session_id)
        pins=list(dict.fromkeys(state["pins"]+ids))
        db.state_update(session_id,pins=pins,selected=ids,selection_mode=False)
        for job in db.jobs_for_session(session_id):
            if job["kind"]!="medium" or job["generation"]!=state["generation"]:
                continue
            if job["church_id"] not in pins:
                cancel(job["job_id"])
                db.update_job(job["job_id"],visible=0)
            elif job["church_id"] in ids:
                db.update_job(job["job_id"],visible=1)
        for cid in ids:
            completed=next((j for j in reversed(db.jobs_for_session(session_id)) if j["kind"]=="medium"
                           and j["church_id"]==cid and j["generation"]==state["generation"] and j["status"]=="complete"
                           and _age_days(j.get("verified_at") or j.get("finished_at"))<7
                           and json.loads(j.get("result_json") or "{}").get("research_version")==5),None)
            if not completed:
                submit(session_id,cid,"medium")
            else:
                _seed_questions(session_id,_church(session_id,cid))
        memory.bump_table(session_id)
    return {"jobs":status(session_id),"research":db.session_state(session_id)}

def pin(session_id: str, church_id: str, pinned: bool) -> dict:
    _church(session_id,church_id)
    with _lock:
        state=db.session_state(session_id)
        pins=[cid for cid in state["pins"] if cid!=church_id]
        if pinned:
            pins.append(church_id)
        db.state_update(session_id,pins=pins)
        if pinned:
            submit(session_id,church_id,"medium")
        else:
            for job in db.jobs_for_session(session_id):
                if job["church_id"]==church_id and job["kind"]=="medium" and job["status"] in ("pending","running"):
                    cancel(job["job_id"])
        memory.bump_table(session_id)
    return {"jobs":status(session_id),"research":db.session_state(session_id)}

def status(session_id: str) -> list[dict]:
    state=db.session_state(session_id)
    latest={}
    for job in db.jobs_for_session(session_id):
        if job["visible"] and job["generation"]==state["generation"]:
            latest[(job["church_id"],job["kind"])]=job
    out=[]
    for job in latest.values():
        try:
            name=_church(session_id,job["church_id"]).name
        except KeyError:
            name=job["church_id"]
        total=job["total"] or 0
        pct=round(100*(job["done"] or 0)/total) if total else None
        elapsed=max(0,round(_age_days(job["started_at"])*86400))
        out.append({"job_id":job["job_id"],"church_id":job["church_id"],"name":name,"kind":job["kind"],
                    "status":job["status"],"done":job["done"] or 0,"total":total,"label":job["label"] or "",
                    "pct":100 if job["status"]=="complete" else pct,"elapsed_seconds":elapsed,
                    "report_url":f"/reports/{job['job_id']}" if job.get("report_path") else None})
    return out

def _run_medium(sid,cid,jid):
    from app.stage2.summary import medium_search
    if is_cancelled(jid):
        return
    try:
        church=_church(sid,cid)
        db.update_job(jid,status="running",label=f"Opening {church.name}'s website")
        def progress(done,total,label):
            if not is_cancelled(jid):
                db.update_job(jid,done=done,total=total,label=label,progress=100*done/total if total else 0)
        card=medium_search(church,memory.to_profile(sid),progress=progress,cancelled=lambda:is_cancelled(jid))
        if card.get("cancelled") or is_cancelled(jid):
            return
        mr=card["match"]
        result={key:card[key] for key in ("settled","open","pages","deep_dive_candidate","reason","facts","staff","resources","coverage") if key in card}
        result.update(fit=mr.fit,score=mr.score,new_evidence=len(card["evidence"]),research_version=5)
        with _lock:
            if is_cancelled(jid):
                return
            if db.get_job(jid)["visible"]:
                _seed_questions(sid,_church(sid,cid))
            db.update_job(jid,status="complete",progress=100,label=f"Website research for {church.name} ready",
                          result_json=json.dumps(result,default=str),finished_at=_now().isoformat(),verified_at=min((p["checked_at"] for p in card.get("pages",[]) if p.get("checked_at")),default=_now().isoformat()))
            memory.bump_table(sid)
    except Exception as exc:
        log.exception("Medium job failed")
        if not is_cancelled(jid):
            db.update_job(jid,status="error",label=f"Could not read website ({type(exc).__name__})",finished_at=_now().isoformat())
            memory.bump_table(sid)

def _run_deep(sid,cid,jid):
    from app.stage3.agent import deep_search
    if is_cancelled(jid):
        return
    try:
        church=_church(sid,cid)
        db.update_job(jid,label=f"Deep dive on {church.name} — reading pages and sermons")
        deep_search(church,memory.to_profile(sid),jid)
        if is_cancelled(jid):
            return
        _answer_open_questions(sid,cid,cancelled=lambda:is_cancelled(jid))
        with _lock:
            if is_cancelled(jid):
                return
            memory.bump_table(sid)
            job=db.get_job(jid)
            if job["status"]=="complete":
                db.update_job(jid,verified_at=job.get("verified_at") or _now().isoformat())
                _announce(sid,cid,"deep",jid,False)
            elif job.get("report_path"):
                chat.post(sid,"The deep dive stopped early. The partial report contains what I found and the remaining gaps.",
                          {"report_url":f"/reports/{jid}","church_id":cid})
    except Exception as exc:
        log.exception("Deep job failed")
        if not is_cancelled(jid):
            db.update_job(jid,status="error",label=f"Deep dive stopped ({type(exc).__name__})",finished_at=_now().isoformat())
            chat.post(sid,"The deep dive stopped early. Its status shows what happened; you can try again.",{"kind":"error","church_id":cid})

def _answer_open_questions(sid,cid,*,cancelled=lambda:False):
    from app import qa
    for question in qa.open_questions(sid,cid):
        if cancelled():
            return
        if question["status"]!="open":
            continue
        try:
            answer=qa.answer(sid,cid,question["text"],record=False)
            if answer.get("confident") and not cancelled():
                db.question_update(question["id"],status="answered",answer=answer["answer"])
        except Exception:
            log.exception("Question answer failed")

def _seed_questions(sid,church):
    from app import qa
    from app.features import all_features,settled_open
    feats=all_features()
    prefs=[p.feature for p in memory.to_profile(sid).preferences if p.weight in ("important","dealbreaker")]
    _,gaps=settled_open(church.evidence,prefs)
    for fid in gaps[:4]:
        label=feats.get(fid,{}).get("label",fid)
        qa.add_question(sid,church.church_id,f"What does {church.name} say about {label.lower()}?")
    return len(gaps[:4])

def _announce(sid,cid,kind,jid,reused=False):
    with _lock:
        if is_cancelled(jid):
            return
        job=db.get_job(jid)
        if not job["visible"] or job["announced"] or kind!="deep":
            return
        db.update_job(jid,announced=1)
        church=_church(sid,cid)
        try:
            partial=bool(json.loads(job.get("result_json") or "{}").get("limitations"))
        except ValueError:
            partial=True
        wording="A partial deep-dive report" if partial else "The deep dive"
        chat.post(sid,f"{wording} on {church.name} is ready. The report includes sources, coverage and what remains unknown.",
                  {"kind":"deep_done","church_id":cid,"report_url":f"/reports/{jid}"})

def purge_expired():
    count=db.research_purge()
    for kind,days in KEEP_DAYS.items():
        count+=db.jobs_purge(kind,(_now()-timedelta(days=days)).isoformat())
    return count
