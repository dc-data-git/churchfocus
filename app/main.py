"""FastAPI app — routes only (INTERFACES §5)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.models import Church

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Church Search", lifespan=lifespan)
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
static_dir = APP_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# In-memory Stage 1 results for the session (Church objects rebuilt each search).
_session_results: dict[str, list[tuple[Church, object]]] = {}
_interviewers: dict[str, object] = {}


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
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
            "title": "Church Search",
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


@app.post("/api/chat")
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
    turn = iv.next_turn(text)
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


@app.post("/api/profile/confirm")
async def api_profile_confirm(request: Request):
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        body = await request.json()
        session_id = body["session_id"]
    else:
        form = await request.form()
        session_id = str(form["session_id"])

    iv = _get_interviewer(session_id)
    if iv._phase not in ("readback", "done"):  # noqa: SLF001 — confirm endpoint may follow read-back in UI
        iv._phase = "readback"
    turn = iv.next_turn("Yes, that's right")
    profile = iv.profile()
    db.save_profile(profile)
    return JSONResponse(profile.model_dump(mode="json") | {"done": turn.get("done", True)})


@app.post("/api/search")
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

    results = light_search(profile)
    _session_results[session_id] = results
    out = [
        {"church": church.model_dump(mode="json"), "match": match.model_dump(mode="json")}
        for church, match in results
    ]
    return JSONResponse(out)


@app.get("/results/{session_id}", response_class=HTMLResponse)
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
            "title": "Results — Church Search",
            "session_id": session_id,
            "profile": profile,
            "origin_text": (profile.origin or {}).get("text", "") if profile else "",
            "results": rows,
        },
    )


@app.post("/api/medium")
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
            card = medium_search(church, profile)
        render_cards.append(card)
        cards.append(_serialize_card(card))

    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
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


@app.post("/api/deep")
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
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return templates.TemplateResponse(request, "report.html", {"report": data, "job_id": job_id})
    return HTMLResponse("Report still generating…", status_code=202)


@app.get("/api/log/{session_id}")
def api_log(session_id: str):
    from app.log import read_session

    return JSONResponse(read_session(session_id))
