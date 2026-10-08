"""Stage 3 agent tools (INTERFACES §4). Every tool takes ctx first and logs StepLog."""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app import db
from app.config import get_settings
from app.denom.kb import get_kb
from app.features import all_features, feature, settled_open
from app.log import write_step
from app.llm import web_search as llm_web_search
from app.models import Church, Evidence, StepLog
from app.stage1 import denomination as denom_module
from app.stage3 import sermons
from app.evidence_rules import marriage_rule_violation, value_ok
from app.web import Blocked, fetch

COVERAGE_AREAS = {
    "identity_governance": "Identity, affiliation, governance and networks",
    "public_staff": "Full publicly listed staff names and positions",
    "services_worship": "Service schedules, formats and worship practices",
    "ministries_community": "Active ministries, small-group ministry and community work",
    "stated_beliefs": "Published faith and mission statements",
    "sermon_teaching": "Reusable sermon collection and teaching analysis",
    "history_public_context": "History, leadership changes and relevant public reporting",
}


def cancelled(ctx: dict) -> bool:
    from app.jobs import is_cancelled
    try:
        return is_cancelled(ctx["job_id"])
    except KeyError:
        return False


def review_coverage(ctx: dict, area: str, status: str, summary: str, urls: list[str] | None = None) -> dict:
    """Record investigation coverage separately from feature evidence."""
    if area not in COVERAGE_AREAS or status not in {"supported", "partial", "not_found"}:
        return {"error": "Unknown coverage area/status"}
    urls = list(dict.fromkeys(urls or []))
    if status == "supported" and (not urls or any(u not in ctx.get("fetched_texts", {}) for u in urls)):
        return {"error": "Supported coverage requires fetched source URLs"}
    if area == "public_staff" and status == "supported":
        for url in urls:
            text = ctx.get("fetched_texts", {}).get(url, "")
            if len(text) > MODEL_TEXT_CHARS:
                end = 0
                for a, b in sorted(ctx.get("source_views", {}).get(url, [])):
                    if a > end:
                        break
                    end = max(end, b)
                if end < len(text):
                    return {"error": "Full staff coverage requires inspecting remaining source chunks with read_source", "url": url, "next_offset": end, "total_chars": len(text)}
    ctx["coverage"][area] = {"status": status, "summary": summary, "urls": urls}
    _log_tool(ctx, action="review_coverage", why=ctx.pop("_tool_why", ""), input={"area": area, "status": status}, result_summary=summary)
    return {"ok": True, "coverage": ctx["coverage"][area]}


_VERBATIM_THRESHOLD = 90
MODEL_TEXT_CHARS = 12000     # R6: what the model sees per page; full text stays in ctx for quote checks
MODEL_LINKS = 30


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def quote_verbatim(quote: str, page_text: str, threshold: int = _VERBATIM_THRESHOLD) -> bool:
    q, t = _norm(quote), _norm(page_text)
    if len(q) < 12:
        return False
    return q in t


def make_ctx(
    *,
    session_id: str,
    church_id: str,
    job_id: str,
    profile,
    church: Church | None = None,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "church_id": church_id,
        "job_id": job_id,
        "profile": profile,
        "church": church,
        "coverage": {a: {"status": "unresearched", "summary": "", "urls": []} for a in COVERAGE_AREAS},
        "resources": [], "limitations": [],
        "step": 0,
        "fetched_texts": {},
        "escalations": [],
        "finished": False,
        "finish_summary": "",
        "sermons_analysed": 0,
        "sermon_items": {},      # sermon_id -> item from get_sermons
        "transcripts": {},       # sermon_id -> {text, minutes, speaker, title, ...}
        "sermon_evidence": [],   # Evidence produced by analyse_sermons (the only source of tier D)
        "tool_calls": 0,
        "started_at": time.monotonic(),
    }


def _next_step(ctx: dict[str, Any]) -> int:
    ctx["step"] = int(ctx.get("step", 0)) + 1
    return ctx["step"]


def _log_tool(
    ctx: dict[str, Any],
    *,
    action: str,
    why: str,
    input: dict,
    result_summary: str,
    features_moved: list[str] | None = None,
    ms: int = 0,
) -> None:
    write_step(
        StepLog(
            session_id=ctx["session_id"],
            church_id=ctx["church_id"],
            stage=3,
            step=_next_step(ctx),
            action=action,
            why=why or "Research lifecycle or coverage update",
            input=input,
            result_summary=result_summary,
            features_moved=features_moved or [],
            ms=ms,
        )
    )


def _track_fetched(ctx: dict[str, Any], url: str, text: str) -> None:
    if url and text:
        ctx.setdefault("fetched_texts", {})[url] = text


def read_source(ctx: dict, url: str, query: str = "", offset: int = 0) -> dict:
    """Read a bounded portion of a persisted source without fetching again."""
    if cancelled(ctx):
        return {"cancelled": True}
    text = ctx.get("fetched_texts", {}).get(url)
    if text is None:
        return {"error": "Source has not been fetched or saved for this church"}
    offset = max(0, int(offset))
    if query:
        pos = text.lower().find(query.lower(), offset)
        if pos < 0:
            return {"url": url, "text": "", "not_found": True, "total_chars": len(text)}
        offset = max(0, pos - 500)
    end = min(len(text), offset + MODEL_TEXT_CHARS)
    ctx.setdefault("source_views", {}).setdefault(url, []).append((offset, end))
    _log_tool(ctx, action="read_source", why=ctx.pop("_tool_why", ""), input={"url": url, "query": query, "offset": offset}, result_summary=f"Read characters {offset}–{end} of {len(text)}")
    return {"url": url, "text": text[offset:end], "offset": offset, "next_offset": end, "total_chars": len(text), "truncated": end < len(text)}


def _source_text_for_quote(ctx: dict[str, Any], url: str, quote: str) -> str | None:
    return ctx.get("fetched_texts", {}).get(url)



def _profile_feature_ids(ctx: dict[str, Any]) -> list[str]:
    return [p.feature for p in ctx["profile"].preferences]


def _validate_evidence_items(items: list) -> tuple[list[Evidence], list[str]]:
    accepted: list[Evidence] = []
    errors: list[str] = []
    for raw in items:
        if isinstance(raw, Evidence):
            ev = raw
        elif isinstance(raw, dict):
            try:
                ev = Evidence.model_validate(raw)
            except Exception as exc:
                errors.append(str(exc))
                continue
        else:
            errors.append(f"invalid evidence item type: {type(raw)!r}")
            continue
        if ev.feature not in all_features():
            errors.append(f"unknown feature {ev.feature!r}")
            continue
        if not value_ok(ev.feature, ev.value):   # R10: unstated/unknown are always allowed
            errors.append(f"invalid value {ev.value!r} for {ev.feature}; allowed: {feature(ev.feature)['values']} or unstated")
            continue
        if ev.tier in ("D", "prior"):            # R10: D only from analyse_sermons; priors come from the KB, not the agent
            errors.append(f"{ev.feature}: tier {ev.tier} cannot be recorded directly (sermon observations are recorded by analyse_sermons)")
            continue
        if not ev.url:
            errors.append(f"{ev.feature}: url required for tier {ev.tier}")
            continue
        if not ev.quote and ev.value not in ("unstated", "unknown"):
            errors.append(f"{ev.feature}: quote required")
            continue
        accepted.append(ev)
    return accepted, errors


def fetch_page(ctx: dict[str, Any], url: str) -> dict:
    """Fetch a public page; store text for quote verification."""
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    try:
        if cancelled(ctx):
            return {"cancelled": True}
        if re.search(r"(?:^|[/_-])(calendar|events?)(?:[/_.?=-]|$)", url, re.I):
            ctx.setdefault("resources", []).append({"kind": "calendar", "url": url})
            return {"url": url, "text": "", "links": [], "resource_only": True, "note": "Calendar entries are outside deep research; retained for targeted questions."}
        page = fetch(url, max_chars=100000, max_age_days=30)
        if cancelled(ctx):
            return {"cancelled": True}
        if not page.get("from_cache"):
            ctx["sources_refreshed"] = True
        if page.get("text") and page.get("status", 200) == 200:
            db.research_put(ctx["church_id"], page.get("url", url), page["text"], scope="deep", checked_at=page.get("checked_at"))
        _track_fetched(ctx, page.get("url", url), page.get("text", ""))
        ctx.setdefault("source_views", {}).setdefault(page.get("url", url), []).append((0, min(len(page.get("text", "")), MODEL_TEXT_CHARS)))
        ms = int((time.perf_counter() - t0) * 1000)
        _log_tool(
            ctx,
            action="fetch_page",
            why=why,
            input={"url": url},
            result_summary=f"status={page.get('status')} chars={len(page.get('text', ''))}",
            ms=ms,
        )
        return _for_model(page)
    except Blocked as exc:
        ms = int((time.perf_counter() - t0) * 1000)
        _log_tool(ctx, action="fetch_page", why=why, input={"url": url}, result_summary=f"blocked: {exc}", ms=ms)
        return {"url": url, "status": 0, "text": "", "links": [], "error": str(exc)}


def _for_model(page: dict) -> dict:
    """R6: keep the conversation small — the model sees a trimmed page; ctx keeps the full text."""
    text = page.get("text") or ""
    out = {k: page.get(k) for k in ("url", "status", "error") if page.get(k) is not None}
    out["text"] = text[:MODEL_TEXT_CHARS] + (f"\n…[truncated; {len(text)} chars total]" if len(text) > MODEL_TEXT_CHARS else "")
    out["total_chars"] = len(text)
    out["next_offset"] = min(len(text), MODEL_TEXT_CHARS)
    out["truncated"] = len(text) > MODEL_TEXT_CHARS
    out["links"] = [{"href": l.get("href"), "text": (l.get("text") or "")[:60]} for l in (page.get("links") or [])[:MODEL_LINKS]]
    return out


def search_web(ctx: dict[str, Any], query: str) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    if cancelled(ctx):
        return {"cancelled": True}
    results = llm_web_search(query)
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="search_web",
        why=why,
        input={"query": query},
        result_summary=f"{len(results)} results",
        ms=ms,
    )
    return {"results": results}


def wayback_snapshots(ctx: dict[str, Any], url: str, years: list[int]) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    snapshots: list[dict[str, str]] = []
    if years:
        year_from = min(years)
        year_to = max(years)
        cdx_url = (
            "http://web.archive.org/cdx/search/cdx"
            f"?url={url}/*&from={year_from}&to={year_to}&output=json"
            "&filter=statuscode:200&collapse=timestamp:6"
        )
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(cdx_url)
                if resp.status_code == 200:
                    rows = resp.json()
                    for row in rows[1:41]:   # cap: enough to see tenure / statement changes
                        if len(row) >= 2:
                            ts, snap_url = row[1], row[2] if len(row) > 2 else url
                            snapshots.append(
                                {
                                    "timestamp": ts,
                                    "url": f"https://web.archive.org/web/{ts}/{snap_url}",
                                }
                            )
        except httpx.HTTPError:
            pass
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="wayback_snapshots",
        why=why,
        input={"url": url, "years": years},
        result_summary=f"{len(snapshots)} snapshots",
        ms=ms,
    )
    return {"snapshots": snapshots}


def denomination_lookup(ctx: dict[str, Any], name_or_id: str) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    kb = get_kb()
    if name_or_id in kb.groups:
        group = kb.get(name_or_id)
        result = {"id": name_or_id, "name": group.get("name") or group.get("census_name"), "match_score": 100}
    else:
        hits = kb.find(name_or_id, k=3)
        result = {"matches": hits}
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="denomination_lookup",
        why=why,
        input={"name_or_id": name_or_id},
        result_summary=json.dumps(result)[:200],
        ms=ms,
    )
    return result


def denomination_locator_search(ctx: dict[str, Any], church_name: str, city: str) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    kb = get_kb()
    guess = denom_module._locator_search(church_name, city, kb)
    if guess is None:
        out = {"found": False}
    else:
        out = {
            "found": True,
            "denomination_id": guess.denomination_id,
            "label": guess.label,
            "confidence": guess.confidence,
            "method": guess.method,
        }
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="denomination_locator_search",
        why=why,
        input={"church_name": church_name, "city": city},
        result_summary=json.dumps(out)[:200],
        ms=ms,
    )
    return out


def find_sermon_feeds(ctx: dict[str, Any]) -> dict:
    """Sermon/media feeds for THIS church (ctx church; the model never supplies the church — R6)."""
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    church = ctx.get("church")
    if church is None or not church.website:
        result = {"feeds": [], "note": "no website on file"}
    else:
        result = sermons.find_sermon_feeds(church)
    feeds = result.get("feeds", [])[:15]
    _log_tool(ctx, action="find_sermon_feeds", why=why, input={"church_id": ctx.get("church_id")},
              result_summary=f"{len(feeds)} feeds", ms=int((time.perf_counter() - t0) * 1000))
    return {"feeds": feeds}


def get_sermons(ctx: dict[str, Any], feed_url: str, limit: int = 25) -> dict:
    """Parse a feed; items are stored in ctx and returned to the model as sermon_ids (no copying of data)."""
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    result = sermons.get_sermons(feed_url, min(limit, get_settings().deep_max_sermons))
    items = result.get("items", [])
    store = ctx.setdefault("sermon_items", {})
    listing = []
    for it in items:
        sid = f"s{len(store) + 1}"
        store[sid] = it
        source_url = it.get("page_url") or it.get("transcript_url") or it.get("audio_url")
        saved = next((v for v in ctx.get("saved_sources", []) if v.get("kind") == "sermon" and v.get("url") == source_url), None)
        if saved:
            ctx.setdefault("transcripts", {})[sid] = {**saved, "page_url": saved["url"]}
        listing.append({"sermon_id": sid, "title": it.get("title", "")[:100], "date": it.get("date", "")[:10],
                        "speaker": it.get("speaker", ""), "has_audio": bool(it.get("audio_url")), "has_video": bool(it.get("video_url")),
                        "has_transcript": bool(it.get("transcript_url") or it.get("text"))})
    _log_tool(ctx, action="get_sermons", why=why, input={"feed_url": feed_url, "limit": limit},
              result_summary=f"{len(items)} items" + (f" ({result['error']})" if result.get("error") else ""),
              ms=int((time.perf_counter() - t0) * 1000))
    return {"feed_url": feed_url, "sermons": listing, **({"error": result["error"]} if result.get("error") else {})}


def transcribe_sermons(ctx: dict[str, Any], sermon_ids: list[str]) -> dict:
    """Transcribe up to 3 in parallel; enforces DEEP_MAX_SERMONS across the whole job (R6). Transcripts stay in ctx."""
    from concurrent.futures import ThreadPoolExecutor

    from app.config import get_settings

    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    store, done = ctx.setdefault("sermon_items", {}), ctx.setdefault("transcripts", {})
    room = max(0, get_settings().deep_max_sermons - len({v.get("page_url") or key for key, v in done.items()}))
    todo = [sid for sid in dict.fromkeys(sermon_ids) if sid in store and sid not in done][:room]
    skipped = [sid for sid in sermon_ids if sid not in todo and sid not in done]

    def work(sid: str) -> tuple[str, dict]:
        if cancelled(ctx):
            return sid, {"text": "", "source": "cancelled"}
        try:
            return sid, sermons.transcribe_sermon(store[sid], church_id=ctx.get("church_id"))
        except Exception as e:   # one bad download must not kill the batch
            return sid, {"text": "", "minutes": 0.0, "source": "error", "error": f"{type(e).__name__}: {e}"[:200]}

    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for sid, r in pool.map(work, todo):
            item = store[sid]
            if cancelled(ctx):
                break
            if r.get("text"):
                ctx["sources_refreshed"] = True
                source_url = item.get("page_url") or item.get("transcript_url") or item.get("audio_url") or f"sermon:{sid}"
                db.research_put(ctx["church_id"], source_url, r["text"], scope="deep", kind="sermon", title=item.get("title", ""), speaker=r.get("speaker") or item.get("speaker", ""), published_at=item.get("date", ""))
                done[sid] = {**r, "title": item.get("title", ""), "date": item.get("date", ""),
                             "speaker": r.get("speaker") or item.get("speaker", ""), "page_url": source_url}
                _track_fetched(ctx, item.get("page_url") or f"sermon:{sid}", r["text"])
            results.append({"sermon_id": sid, "minutes": round(float(r.get("minutes") or 0), 1),
                            "chars": len(r.get("text") or ""), "source": r.get("source"), **({"error": r["error"]} if r.get("error") else {})})
    note = f"budget: {len(done)} of {get_settings().deep_max_sermons} sermons transcribed"
    if skipped:
        note += f"; skipped {len(skipped)} (unknown id, already done, or over budget)"
    _log_tool(ctx, action="transcribe_sermons", why=why, input={"sermon_ids": sermon_ids},
              result_summary=f"{sum(1 for r in results if r['chars'])} transcribed; {note}",
              ms=int((time.perf_counter() - t0) * 1000))
    return {"results": results, "note": note}


def transcribe_sermon(ctx: dict[str, Any], sermon_id: str) -> dict:
    """Single-sermon convenience wrapper (INTERFACES name)."""
    return transcribe_sermons(ctx, [sermon_id])


_SERMON_FEATURES = {"women.preach", "logistics.sermon_length", "preaching.style", "preaching.audience",
                    "preaching.scripture_density", "preaching.politics_frequency"}


def analyse_sermons(ctx: dict[str, Any], sermon_ids: list[str] | None = None, features: list[str] | None = None) -> dict:
    """Analyse transcribed sermons and RECORD the observed evidence (tier D) plus verbatim stated positions (tier A)."""
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    done = ctx.get("transcripts", {})
    ids = []
    seen_urls = set()
    for sid in (sermon_ids or list(done)):
        if sid not in done:
            continue
        url = done[sid].get("page_url") or sid
        if url not in seen_urls:
            ids.append(sid)
            seen_urls.add(url)
    analysed = set(ctx.setdefault("analysed_ids", []))
    feats = [f for f in (features or list(all_features())) if f in all_features()] or list(_SERMON_FEATURES)
    texts = [{**done[sid], "sermon_id": sid} for sid in ids]
    result = sermons.analyse_sermons(texts, list(set(feats) | _SERMON_FEATURES), church_id=ctx.get("church_id"), cancelled=lambda: cancelled(ctx))
    if cancelled(ctx):
        return {"cancelled": True}
    ctx["analysed_ids"] = sorted(analysed | set(ids))
    ctx["sermons_analysed"] = len(ctx["analysed_ids"])

    observed: list[Evidence] = list(result.get("evidence", []))
    stated: list[Evidence] = []
    for sid, a in zip(ids, result.get("analyses", [])):
        src = done[sid]
        for pos in a.get("stated_positions") or []:
            if not isinstance(pos, dict):
                continue
            fid, val, quote = pos.get("feature", ""), str(pos.get("value", "")), str(pos.get("quote", ""))
            if (value_ok(fid, val) and quote_verbatim(quote, src["text"]) and not marriage_rule_violation(fid, val, quote)):
                stated.append(Evidence(feature=fid, value=val, tier="A", how="stated", source_kind="sermon_transcript",
                                       quote=quote[:300], url=src.get("page_url") or "", note=f"sermon: {src.get('title', '')[:80]}"))
    new = observed + [e for e in stated if e.url]
    if new and not cancelled(ctx):
        db.add_evidence(ctx["church_id"], new)
        ctx.setdefault("sermon_evidence", []).extend(observed)
        church = ctx.get("church")
        if church is not None:
            ctx["church"] = church.model_copy(update={"evidence": church.evidence + new})
    settled, open_f = settled_open(db.get_evidence(ctx["church_id"]), _profile_feature_ids(ctx))
    moved = sorted({e.feature for e in new})
    _log_tool(ctx, action="analyse_sermons", why=why, input={"sermon_ids": ids, "features": feats},
              result_summary=f"{len(ids)} analysed; recorded {len(new)} evidence ({', '.join(moved)})",
              features_moved=moved, ms=int((time.perf_counter() - t0) * 1000))
    return {"analysed": len(ids), "recorded": [{"feature": e.feature, "value": e.value, "tier": e.tier, "note": e.note} for e in new],
            "settled": settled, "open": open_f}


def record_evidence(ctx: dict[str, Any], evidence: list) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    if cancelled(ctx):
        return {"cancelled": True}
    accepted, errors = _validate_evidence_items(evidence)
    if errors and not accepted:
        ms = int((time.perf_counter() - t0) * 1000)
        _log_tool(
            ctx,
            action="record_evidence",
            why=why,
            input={"count": len(evidence)},
            result_summary=f"rejected: {'; '.join(errors)}",
            ms=ms,
        )
        return {"ok": False, "errors": errors}

    stored: list[Evidence] = []
    quote_errors: list[str] = []
    for ev in accepted:
        if ev.quote and ev.tier in ("A", "B", "C"):
            source = _source_text_for_quote(ctx, ev.url, ev.quote)
            if source is None or not quote_verbatim(ev.quote, source):
                quote_errors.append(f"quote not verbatim for {ev.feature}")
                write_step(
                    StepLog(
                        session_id=ctx["session_id"],
                        church_id=ctx["church_id"],
                        stage=3,
                        step=_next_step(ctx),
                        action="correction",
                        why=why,
                        input={"feature": ev.feature, "quote": ev.quote[:120]},
                        result_summary="quote failed verbatim re-check",
                        features_moved=[],
                    )
                )
                continue
        why_not = marriage_rule_violation(ev.feature, ev.value, ev.quote)   # after the verbatim check (R10)
        if why_not:
            quote_errors.append(f"{ev.feature}: {why_not}")
            continue
        stored.append(ev)

    if quote_errors and not stored:
        ms = int((time.perf_counter() - t0) * 1000)
        _log_tool(
            ctx,
            action="record_evidence",
            why=why,
            input={"count": len(evidence)},
            result_summary=f"quote rejected: {'; '.join(quote_errors)}",
            ms=ms,
        )
        return {"ok": False, "errors": quote_errors, "correction": True}

    if stored:
        db.add_evidence(ctx["church_id"], stored)
        church = ctx.get("church")
        if church is not None:
            church = church.model_copy(update={"evidence": church.evidence + stored})
            ctx["church"] = church

    feature_ids = _profile_feature_ids(ctx)
    all_ev = db.get_evidence(ctx["church_id"])
    settled, open_f = settled_open(all_ev, feature_ids)
    moved = [e.feature for e in stored]
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="record_evidence",
        why=why,
        input={"count": len(stored)},
        result_summary=f"stored {len(stored)}; settled={len(settled)} open={len(open_f)}",
        features_moved=moved,
        ms=ms,
    )
    return {
        "ok": True,
        "stored": len(stored),
        "settled": settled,
        "open": open_f,
        "errors": errors + quote_errors,
    }


def escalate(ctx: dict[str, Any], reason: str, message: str, questions: list[str]) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    from app.models import Escalation

    allowed = {"pastoral_or_crisis", "dealbreaker_conflict", "low_denom_confidence", "budget_exhausted"}
    if reason not in allowed:
        reason = "dealbreaker_conflict"
    esc = Escalation(reason=reason, message=message, questions_to_ask=list(questions))
    ctx.setdefault("escalations", []).append(esc)
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="escalate",
        why=why,
        input={"reason": reason, "questions": len(questions)},
        result_summary=message[:200],
        ms=ms,
    )
    return {"ok": True, "reason": reason}


def finish(ctx: dict[str, Any], summary: str) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    gaps = [a for a, v in ctx.get("coverage", {}).items() if v["status"] == "unresearched"]
    if gaps and not ctx.get("finish_nudged"):
        ctx["finish_nudged"] = True
        return {"ok": False, "unresearched": gaps, "instruction": "Investigate minimum coverage and call review_coverage; preferences are not the research boundary. If sources/budget genuinely prevent completion, call finish again for an explicitly partial report."}
    if gaps:
        ctx.setdefault("limitations", []).append("Not researched: " + ", ".join(COVERAGE_AREAS[a] for a in gaps))
    ctx["finished"] = True
    ctx["finish_summary"] = summary
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="finish",
        why=why,
        input={"summary_len": len(summary)},
        result_summary=summary[:200],
        ms=ms,
    )
    return {"ok": True, "summary": summary}
