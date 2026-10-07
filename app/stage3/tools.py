"""Stage 3 agent tools (INTERFACES §4). Every tool takes ctx first and logs StepLog."""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from rapidfuzz import fuzz

from app import db
from app.denom.kb import get_kb
from app.features import all_features, feature, settled_open
from app.log import write_step
from app.llm import web_search as llm_web_search
from app.models import Church, Evidence, StepLog
from app.stage1 import denomination as denom_module
from app.stage3 import sermons
from app.web import Blocked, fetch

_VERBATIM_THRESHOLD = 90


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def quote_verbatim(quote: str, page_text: str, threshold: int = _VERBATIM_THRESHOLD) -> bool:
    q, t = _norm(quote), _norm(page_text)
    if len(q) < 12:
        return False
    if q in t:
        return True
    return fuzz.partial_ratio(q, t) >= threshold


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
        "step": 0,
        "fetched_texts": {},
        "escalations": [],
        "finished": False,
        "finish_summary": "",
        "sermons_analysed": 0,
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
            why=why or "",
            input=input,
            result_summary=result_summary,
            features_moved=features_moved or [],
            ms=ms,
        )
    )


def _track_fetched(ctx: dict[str, Any], url: str, text: str) -> None:
    if url and text:
        ctx.setdefault("fetched_texts", {})[url] = text


def _source_text_for_quote(ctx: dict[str, Any], url: str, quote: str) -> str | None:
    texts = ctx.get("fetched_texts", {})
    if url and url in texts:
        return texts[url]
    for text in texts.values():
        if quote_verbatim(quote, text):
            return text
    return None


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
        allowed = feature(ev.feature)["values"]
        if ev.value not in allowed:
            errors.append(f"invalid value {ev.value!r} for {ev.feature}")
            continue
        accepted.append(ev)
    return accepted, errors


def fetch_page(ctx: dict[str, Any], url: str) -> dict:
    """Fetch a public page; store text for quote verification."""
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    try:
        page = fetch(url)
        _track_fetched(ctx, page.get("url", url), page.get("text", ""))
        ms = int((time.perf_counter() - t0) * 1000)
        _log_tool(
            ctx,
            action="fetch_page",
            why=why,
            input={"url": url},
            result_summary=f"status={page.get('status')} chars={len(page.get('text', ''))}",
            ms=ms,
        )
        return page
    except Blocked as exc:
        ms = int((time.perf_counter() - t0) * 1000)
        _log_tool(ctx, action="fetch_page", why=why, input={"url": url}, result_summary=f"blocked: {exc}", ms=ms)
        return {"url": url, "status": 0, "text": "", "links": [], "error": str(exc)}


def search_web(ctx: dict[str, Any], query: str) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
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
                    for row in rows[1:]:
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


def find_sermon_feeds(ctx: dict[str, Any], church: Church | dict[str, Any]) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    if isinstance(church, dict):
        church = Church.model_validate(church)
    result = sermons.find_sermon_feeds(church)
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="find_sermon_feeds",
        why=why,
        input={"church_id": church.church_id},
        result_summary=f"{len(result.get('feeds', []))} feeds",
        ms=ms,
    )
    return result


def get_sermons(ctx: dict[str, Any], feed_url: str, limit: int) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    result = sermons.get_sermons(feed_url, limit)
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="get_sermons",
        why=why,
        input={"feed_url": feed_url, "limit": limit},
        result_summary=f"{len(result.get('items', []))} items",
        ms=ms,
    )
    return result


def transcribe_sermon(ctx: dict[str, Any], item: dict) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    result = sermons.transcribe_sermon(item, church_id=ctx.get("church_id"))
    text = result.get("text", "")
    if text:
        url = item.get("page_url") or item.get("title") or "sermon"
        _track_fetched(ctx, url, text)
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="transcribe_sermon",
        why=why,
        input={"title": item.get("title", "")},
        result_summary=f"minutes={result.get('minutes', 0)} source={result.get('source', '')}",
        ms=ms,
    )
    return result


def analyse_sermons(ctx: dict[str, Any], texts: list[dict], features: list[str]) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
    result = sermons.analyse_sermons(texts, features, church_id=ctx.get("church_id"))
    ctx["sermons_analysed"] = int(ctx.get("sermons_analysed", 0)) + int(result.get("sermons_analysed", 0))
    moved = [e.feature for e in result.get("evidence", [])]
    ms = int((time.perf_counter() - t0) * 1000)
    _log_tool(
        ctx,
        action="analyse_sermons",
        why=why,
        input={"sermon_count": len(texts), "features": features},
        result_summary=f"{result.get('sermons_analysed', 0)} analysed, {len(moved)} features",
        features_moved=moved,
        ms=ms,
    )
    return {"evidence": [e.model_dump(mode="json") for e in result.get("evidence", [])], **result}


def record_evidence(ctx: dict[str, Any], evidence: list) -> dict:
    t0 = time.perf_counter()
    why = ctx.pop("_tool_why", "")
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
