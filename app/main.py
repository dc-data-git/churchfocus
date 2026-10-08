"""FastAPI app — routes only (INTERFACES §5)."""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app import db
from app.models import Church

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    _check_settings()
    try:   # jobs from a previous run lost their worker threads; saved results past retention are dropped (D43)
        from app import jobs
        db.jobs_orphan_cleanup()
        jobs.purge_expired()
    except Exception:
        logging.getLogger("app").exception("job cleanup failed")
    yield


def _check_settings() -> None:
    """Fail loudly at startup instead of silently skipping every model call (review finding)."""
    import logging

    from app.config import get_settings

    s = get_settings()
    missing = []
    if s.llm_backend == "openai":
        missing += [n for n, v in (("OPENAI_API_KEY", s.openai_api_key), ("OPENAI_MODEL_FAST", s.openai_model_fast),
                                   ("OPENAI_MODEL_STRONG", s.openai_model_strong)) if not v]
    if not s.google_places_api_key:
        missing.append("GOOGLE_PLACES_API_KEY")
    if missing:
        logging.getLogger("app").error("Missing settings in .env: %s — those features will fail.", ", ".join(missing))
        print(f"\n*** ChurchFocus: missing in .env: {', '.join(missing)} ***\n")


app = FastAPI(title="ChurchFocus", lifespan=lifespan)
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
static_dir = APP_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# In-memory Stage 1 results for the session (Church objects rebuilt each search).
_session_results: dict[str, list[tuple[Church, object]]] = {}
_interviewers: dict[str, object] = {}


# ---------------------------------------------------------------- v2 routes (REDESIGN.md §9)
def _v2_call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except (KeyError,ValueError) as e:
        return JSONResponse({"error":str(e)},status_code=422)
    except NotImplementedError as e:
        return JSONResponse({"error": "not_implemented", "task": str(e)}, status_code=501)


@app.get("/", response_class=HTMLResponse)
def app_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "app.html", {"title": "ChurchFocus"})


@app.post("/api/chat")
async def api_chat_v2(request: Request):
    body = await request.json()
    from app.stage0 import conversation

    sid = body.get("session_id") or uuid.uuid4().hex
    out = await run_in_threadpool(lambda: _v2_call(conversation.turn, sid, body.get("text")))
    if isinstance(out, JSONResponse):
        return out
    return JSONResponse({"session_id": sid, **out})


@app.get("/api/state/{sid}")
def api_state(sid: str, since: int = 0):
    from app import chat

    out = {"messages": chat.since(sid, since), "jobs": [], "table_version": 0, "memory_version": 0}
    try:
        from app import jobs
        out["jobs"] = jobs.status(sid)
    except NotImplementedError:
        pass
    try:
        from app import memory
        out["memory_version"] = memory.version(sid)
        out["table_version"] = memory.table_version(sid)
    except (NotImplementedError, AttributeError, ImportError):
        pass
    out["location"] = memory.location(sid)
    out["research"] = db.session_state(sid)
    return JSONResponse(out)


@app.get("/api/churches/{sid}")
def api_churches(sid: str, radius: float | None = None, sort: str = "fit", page: int = 1, size: int = 10):
    from app.stage1 import search

    from app import memory
    radius = radius if radius is not None else memory.to_profile(sid).max_miles or 15
    out = _v2_call(search.table, sid, radius_mi=min(max(radius, 1), 50), sort=sort, page=page, size=min(max(size, 5), 50))
    return out if isinstance(out, JSONResponse) else JSONResponse(out)


@app.post("/api/know_more")
async def api_know_more(request: Request):
    body = await request.json()
    from app.stage0 import conversation

    from app import jobs
    if body.get("action")=="prepare":
        out = await run_in_threadpool(lambda:_v2_call(jobs.prepare,body["session_id"]))
    else:
        out = await run_in_threadpool(lambda: _v2_call(conversation.know_more, body["session_id"], list(body.get("church_ids") or [])))
    return out if isinstance(out, JSONResponse) else JSONResponse(out)


@app.post("/api/pin")
async def api_pin(request: Request):
    body=await request.json()
    from app import jobs
    if not isinstance(body.get("pinned"),bool):
        return JSONResponse({"error":"pinned must be true or false"},status_code=422)
    out=await run_in_threadpool(lambda:_v2_call(jobs.pin,body["session_id"],body["church_id"],body["pinned"]))
    return out if isinstance(out,JSONResponse) else JSONResponse(out)


@app.post("/api/restart")
async def api_restart(request: Request):
    body=await request.json()
    from app.stage0 import conversation
    out=await run_in_threadpool(lambda:_v2_call(conversation.restart,body["session_id"],body.get("mode","new")))
    return out if isinstance(out,JSONResponse) else JSONResponse(out)


@app.post("/api/radius")
async def api_radius(request: Request):
    body=await request.json()
    from app import memory
    from app.models import MemoryOp
    from app.stage1 import search
    try:
        radius=min(max(float(body["radius"]),1),50)
    except (ValueError,TypeError,KeyError):
        return JSONResponse({"error":"Choose a distance between 1 and 50 miles"},status_code=422)
    sid=body["session_id"]
    loc=memory.location(sid)
    if loc:
        memory.append(sid,[MemoryOp(t=memory.next_turn(sid),op="revise",key="location",val={**loc,"limit_miles":radius},
                                    stance="want",strength=1,conf=1,src="user_edit",why="edited distance filter")])
        search.request_coverage(sid,loc["lat"],loc["lng"],radius)
    return JSONResponse({"location":memory.location(sid)})


@app.get("/api/memory/{sid}")
def api_memory(sid: str):
    from app import memory

    return JSONResponse(_v2_call(memory.plain_summary, sid))


@app.post("/api/memory/{sid}")
async def api_memory_edit(sid: str, request: Request):
    body = await request.json()
    from app import memory

    out = await run_in_threadpool(lambda: _v2_call(memory.user_edit, sid, body.get("key", ""), body.get("text", "")))
    if isinstance(out, JSONResponse):
        return out
    if out:
        return JSONResponse({"error": "Could not apply this edit", "details": out}, status_code=422)
    return JSONResponse(memory.plain_summary(sid))


@app.get("/api/questions/{sid}")
def api_questions(sid: str, church_id: str | None = None):
    from app import qa

    return JSONResponse(qa.open_questions(sid, church_id))


@app.post("/api/questions/{sid}")
async def api_questions_edit(sid: str, request: Request):
    body = await request.json()
    from app import qa

    if body.get("id"):
        if not any(q["id"] == int(body["id"]) for q in qa.open_questions(sid)):
            return JSONResponse({"error": "Question not found"}, status_code=404)
        qa.update_question(int(body["id"]), text=body.get("text"), status=body.get("status"))
    elif body.get("church_id") and body.get("text"):
        qa.add_question(sid, body["church_id"], body["text"])
    return JSONResponse(qa.open_questions(sid))


@app.post("/api/deep")
async def api_deep_v2(request: Request):
    body = await request.json()
    from app import jobs

    out = await run_in_threadpool(lambda: _v2_call(jobs.submit, body["session_id"], body["church_id"], "deep",questions=list(body.get("questions") or [])))
    return out if isinstance(out, JSONResponse) else JSONResponse({"job_id": out})


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/v1", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    from app.stage0.interviewer import Interviewer

    session_id = uuid.uuid4().hex
    iv = Interviewer(session_id)
    _interviewers[session_id] = iv
    turn = iv.next_turn(None)
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "title": "ChurchFocus",
            "session_id": session_id,
            "initial_say": turn.get("say", ""),
            "initial_options": turn.get("options"),
        },
    )


def _get_interviewer(session_id: str):
    from app.stage0.interviewer import Interviewer

    iv = _interviewers.get(session_id)
    if iv is None:
        iv = Interviewer(session_id)
        saved = db.get_profile(session_id)
        if saved:
            iv._profile = saved  # noqa: SLF001 — restore if process restarted mid-session
        _interviewers[session_id] = iv
    return iv


@app.post("/v1/api/chat")
async def api_chat(request: Request):
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        body = await request.json()
        session_id = body.get("session_id") or uuid.uuid4().hex
        text = body.get("text")
    else:
        form = await request.form()
        session_id = str(form.get("session_id") or uuid.uuid4().hex)
        text = form.get("text")
        text = str(text) if text is not None else None

    iv = _get_interviewer(session_id)
    turn = await run_in_threadpool(iv.next_turn, text)   # LLM calls: keep the event loop free (R5)
    payload = {
        "session_id": session_id,
        "say": turn.get("say"),
        "options": turn.get("options"),
        "done": turn.get("done", False),
        "escalation": turn["escalation"].model_dump() if turn.get("escalation") else None,
        "readback": turn.get("done") is False and "right" in (turn.get("say") or "").lower(),
    }

    # Prefer JSON for chat UI (HX + fetch). HTML only when explicitly requested.
    accept = request.headers.get("accept", "")
    if "application/json" in accept or request.headers.get("hx-request"):
        return JSONResponse(payload)
    if "text/html" in accept:
        if payload["escalation"]:
            return HTMLResponse(f'<div class="msg escalation">{payload["say"]}</div>')
        return HTMLResponse(f'<div class="msg bot" data-session="{session_id}">{payload["say"]}</div>')
    return JSONResponse(payload)


@app.post("/v1/api/profile/confirm")
async def api_profile_confirm(request: Request):
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        body = await request.json()
        session_id = body["session_id"]
    else:
        form = await request.form()
        session_id = str(form["session_id"])

    iv = _get_interviewer(session_id)
    if iv._phase == "escalated":  # noqa: SLF001 — a crisis-escalated session must not be pushed through to search (R11)
        return JSONResponse({"error": "escalated", "message": "Please talk with a person first."}, status_code=409)
    if iv._phase not in ("readback", "done"):  # noqa: SLF001 — confirm endpoint may follow read-back in UI
        iv._phase = "readback"
    turn = await run_in_threadpool(iv.next_turn, "Yes, that's right")
    profile = iv.profile()
    db.save_profile(profile)
    return JSONResponse(profile.model_dump(mode="json") | {"done": turn.get("done", True)})


@app.post("/v1/api/search")
async def api_search(request: Request):
    body = await request.json()
    session_id = body["session_id"]
    profile = db.get_profile(session_id)
    if profile is None:
        iv = _interviewers.get(session_id)
        profile = iv.profile() if iv else None
    if profile is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)

    from app.stage1.denomination import light_search
    from app.stage1.places import GeocodeError

    try:
        results = await run_in_threadpool(light_search, profile)   # minutes of network work: off the event loop (R5)
    except GeocodeError as e:
        return JSONResponse({"error": "geocode", "message": str(e)}, status_code=400)
    _session_results[session_id] = results
    out = [
        {"church": church.model_dump(mode="json"), "match": match.model_dump(mode="json")}
        for church, match in results
    ]
    return JSONResponse(out)


@app.get("/v1/results/{session_id}", response_class=HTMLResponse)
def results_page(request: Request, session_id: str) -> HTMLResponse:
    profile = db.get_profile(session_id)
    results = _session_results.get(session_id)
    if results is None and profile is not None:
        from app.stage1.denomination import light_search

        results = light_search(profile)
        _session_results[session_id] = results
    rows = [{"church": c, "match": m} for c, m in (results or [])]
    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "title": "Results — ChurchFocus",
            "session_id": session_id,
            "profile": profile,
            "origin_text": (profile.origin or {}).get("text", "") if profile else "",
            "results": rows,
        },
    )


@app.post("/v1/api/medium")
async def api_medium(request: Request):
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        body = await request.json()
        session_id = body["session_id"]
        church_ids = body.get("church_ids") or []
    else:
        form = await request.form()
        session_id = str(form["session_id"])
        church_ids = form.getlist("church_ids")

    church_ids = list(church_ids)[:5]
    profile = db.get_profile(session_id)
    if profile is None:
        iv = _interviewers.get(session_id)
        profile = iv.profile() if iv else None
    if profile is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)

    stored = {c.church_id: (c, m) for c, m in _session_results.get(session_id, [])}
    cards = []
    try:
        from app.stage2.summary import medium_search
    except ImportError:
        medium_search = None  # type: ignore

    render_cards: list[dict] = []
    for cid in church_ids:
        pair = stored.get(cid)
        if not pair:
            continue
        church, match = pair
        if medium_search is None:
            card = {
                "church": church,
                "match": match,
                "settled": [],
                "open": [],
                "evidence": [],
                "deep_dive_candidate": "possible",
                "reason": "coming",
            }
        else:
            try:
                card = await run_in_threadpool(medium_search, church, profile)   # R5
            except Exception as e:  # one unreachable site must not 500 the whole request
                card = {"church": church, "match": match, "settled": [], "open": [], "evidence": [],
                        "deep_dive_candidate": "weak", "reason": f"Couldn't read this church's website ({type(e).__name__})."}
        render_cards.append(card)
        cards.append(_serialize_card(card))

    accept = request.headers.get("accept", "")
    # HTMX sends Accept: */* — return HTML whenever the request comes from HTMX (R4)
    if request.headers.get("hx-request") or ("text/html" in accept and "application/json" not in accept):
        return templates.TemplateResponse(
            request, "cards.html", {"cards": render_cards, "session_id": session_id}
        )
    return JSONResponse(cards)


def _serialize_card(card: dict) -> dict:
    out = dict(card)
    ch = out.get("church")
    if hasattr(ch, "model_dump"):
        out["church"] = ch.model_dump(mode="json")
    m = out.get("match")
    if hasattr(m, "model_dump"):
        out["match"] = m.model_dump(mode="json")
    evs = out.get("evidence") or []
    out["evidence"] = [e.model_dump(mode="json") if hasattr(e, "model_dump") else e for e in evs]
    return out


@app.post("/v1/api/deep")
async def api_deep(request: Request):
    body = await request.json()
    session_id = body["session_id"]
    church_ids = list(body.get("church_ids") or [])[:3]
    profile = db.get_profile(session_id)
    if profile is None:
        iv = _interviewers.get(session_id)
        profile = iv.profile() if iv else None
    if profile is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)

    stored = {c.church_id: c for c, _m in _session_results.get(session_id, [])}
    from app.stage3.agent import run_deep_search_job

    job_ids: list[str] = []
    for cid in church_ids:
        church = stored.get(cid)
        if church is None:
            continue
        job_id = uuid.uuid4().hex
        # Job row created inside deep_search; pre-create so polling works immediately.
        db.create_job(job_id, session_id, cid)
        run_deep_search_job(church, profile, job_id, background=True)
        job_ids.append(job_id)
    return JSONResponse({"job_ids": job_ids})


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    try:
        job = db.get_job(job_id)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    out = dict(job)
    status = out.get("status")
    if status in ("done", "complete") and out.get("report_path"):
        out["report_url"] = f"/reports/{job_id}"
    from app.log import read_session

    steps = [s for s in read_session(job.get("session_id", "")) if s.get("church_id") == job.get("church_id")]
    out["last_steps"] = steps[-5:]
    try:
        prog = float(out.get("progress") or 0)
    except (TypeError, ValueError):
        prog = 0.0
    if status in ("done", "complete"):
        prog = 100.0
    out["progress"] = prog
    return JSONResponse(out)


@app.get("/reports/{job_id}", response_class=HTMLResponse)
def report_page(request: Request, job_id: str):
    try:
        job = db.get_job(job_id)
    except KeyError:
        return HTMLResponse("Report not found", status_code=404)
    path = job.get("report_path")
    if path and Path(path).is_file():
        return HTMLResponse(Path(path).read_text(encoding="utf-8"))
    # Fallback: template from JSON sibling
    json_path = Path(path).with_suffix(".json") if path else None
    if json_path and json_path.is_file():
        from app.models import ChurchReport
        from app.stage3 import report as report_module

        data = json.loads(json_path.read_text(encoding="utf-8"))
        narrative = data.pop("narrative", None)
        return HTMLResponse(report_module.render_html(ChurchReport.model_validate(data), narrative=narrative))
    return HTMLResponse("Report still generating…", status_code=202)


@app.get("/api/log/{session_id}")
def api_log(session_id: str):
    from app.log import read_session

    return JSONResponse(read_session(session_id))
