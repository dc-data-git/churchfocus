"""Stage 3 deep-search agent loop (INTERFACES §4, ARCHITECTURE §8)."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from app import db
from app.config import get_settings
from app.features import settled_open, all_features
from app.llm import chat_tools, load_prompt
from app.models import Church, PreferenceProfile
from app.stage3 import report as report_module
from app.stage3 import tools

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="deep-search")

def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    props = {**props, "why": {"type": "string", "description": "One sentence: which feature(s) this should move and why."}}
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required + ["why"]}}}


_EVIDENCE_ITEM = {
    "type": "object",
    "properties": {
        "feature": {"type": "string", "description": "A feature id from the list you were given."},
        "value": {"type": "string", "description": "An allowed value for that feature, or 'unstated' if the church's pages do not say."},
        "tier": {"type": "string", "enum": ["A", "B", "C"], "description": "A = church's own words; B = independent source; C = weak third-party signal."},
        "quote": {"type": "string", "description": "Copied word-for-word from a page you fetched (<= 60 words)."},
        "url": {"type": "string", "description": "The page the quote came from."},
        "source_kind": {"type": "string"},
        "how": {"type": "string", "enum": ["stated", "inferred"]},
        "note": {"type": "string"},
    },
    "required": ["feature", "value", "tier", "quote", "url", "source_kind", "how"],
}

_TOOL_SCHEMAS: list[dict[str, Any]] = [
    _fn("fetch_page", "Fetch a public page (text trimmed to ~4,000 chars + up to 30 links).", {"url": {"type": "string"}}, ["url"]),
    _fn("read_source", "Read the next chunk of a saved/fetched source, or locate text; inspect all staff chunks before declaring full staff coverage.", {"url": {"type": "string"}, "query": {"type": "string"}, "offset": {"type": "integer"}}, ["url"]),
    _fn("search_web", "Search the public web; returns titles, URLs and snippets.", {"query": {"type": "string"}}, ["query"]),
    _fn("wayback_snapshots", "List archived snapshots of a URL (leadership tenure, changed statements).",
        {"url": {"type": "string"}, "years": {"type": "array", "items": {"type": "integer"}}}, ["url", "years"]),
    _fn("denomination_lookup", "What a denomination typically holds (a prior, never a fact about this church).",
        {"name_or_id": {"type": "string"}}, ["name_or_id"]),
    _fn("find_sermon_feeds", "Find sermon podcast/RSS/media feeds on THIS church's website.", {}, []),
    _fn("get_sermons", "List sermons from RSS, YouTube video/channel/playlist URLs, including completed livestreams. Returns sermon_ids.",
        {"feed_url": {"type": "string"}, "limit": {"type": "integer"}}, ["feed_url"]),
    _fn("transcribe_sermons", "Transcribe sermons by sermon_id (3 in parallel; total capped per church).",
        {"sermon_ids": {"type": "array", "items": {"type": "string"}}}, ["sermon_ids"]),
    _fn("analyse_sermons", "Analyse transcribed sermons and RECORD what they show (who preaches, length, style, "
        "audience, politics). Omit sermon_ids to analyse all transcribed sermons.",
        {"sermon_ids": {"type": "array", "items": {"type": "string"}}, "features": {"type": "array", "items": {"type": "string"}}}, []),
    _fn("record_evidence", "Store evidence from pages you fetched. Quotes are re-checked word-for-word against the page.",
        {"evidence": {"type": "array", "items": _EVIDENCE_ITEM}}, ["evidence"]),
    _fn("escalate", "Hand a question back to the person (conflicting evidence on a must-have, unclear denomination).",
        {"reason": {"type": "string", "enum": ["dealbreaker_conflict", "low_denom_confidence", "budget_exhausted"]},
         "message": {"type": "string"}, "questions": {"type": "array", "items": {"type": "string"}}},
        ["reason", "message", "questions"]),
    _fn("review_coverage", "Record broad coverage after investigating an area; source URLs must have been fetched.", {"area": {"type": "string", "enum": list(tools.COVERAGE_AREAS)}, "status": {"type": "string", "enum": ["supported", "partial", "not_found"]}, "summary": {"type": "string"}, "urls": {"type": "array", "items": {"type": "string"}}}, ["area", "status", "summary"]),
    _fn("finish", "Stop after broad minimum coverage or report explicit limitations. Summarise in <= 5 sentences.",
        {"summary": {"type": "string"}}, ["summary"]),
]

_TOOL_DISPATCH: dict[str, Callable[..., dict]] = {
    "fetch_page": tools.fetch_page,
    "read_source": tools.read_source,
    "search_web": tools.search_web,
    "wayback_snapshots": tools.wayback_snapshots,
    "denomination_lookup": tools.denomination_lookup,
    "denomination_locator_search": tools.denomination_locator_search,
    "find_sermon_feeds": tools.find_sermon_feeds,
    "get_sermons": tools.get_sermons,
    "transcribe_sermons": tools.transcribe_sermons,
    "transcribe_sermon": tools.transcribe_sermon,
    "analyse_sermons": tools.analyse_sermons,
    "record_evidence": tools.record_evidence,
    "escalate": tools.escalate,
    "finish": tools.finish,
    "review_coverage": tools.review_coverage,
}


def _priority_features(profile: PreferenceProfile) -> list[str]:
    return [p.feature for p in profile.preferences if p.weight in ("dealbreaker", "important")]


def _target_features(profile: PreferenceProfile) -> list[str]:
    return list(all_features())


def _important_settled(profile: PreferenceProfile, evidence) -> bool:
    feature_ids = _priority_features(profile) or _target_features(profile)   # no must-haves -> research nice-to-haves
    if not feature_ids:
        return True
    settled, _ = settled_open(evidence, feature_ids)
    return len(settled) == len(feature_ids)


def _budget_remaining(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    elapsed_min = (time.monotonic() - ctx["started_at"]) / 60.0
    return {
        "tool_calls_used": ctx.get("tool_calls", 0),
        "tool_calls_max": settings.deep_max_tool_calls,
        "minutes_used": round(elapsed_min, 2),
        "minutes_max": settings.deep_max_minutes,
        "sermons_analysed": ctx.get("sermons_analysed", 0),
        "sermons_max": settings.deep_max_sermons,
    }


def _budget_exhausted(ctx: dict[str, Any]) -> bool:
    b = _budget_remaining(ctx)
    return b["tool_calls_used"] >= b["tool_calls_max"] or b["minutes_used"] >= b["minutes_max"]


def _state_message(church: Church, profile: PreferenceProfile, ctx: dict[str, Any]) -> str:
    evidence = db.get_evidence(church.church_id)
    feature_ids = _target_features(profile)
    settled, open_f = settled_open(evidence, feature_ids)
    priority = _priority_features(profile)
    priority_open = [f for f in open_f if f in priority]
    from app.features import feature as _feature

    return "CURRENT STATE (replaces any earlier state):\n" + json.dumps(
        {
            "church": {"name": church.name, "address": church.address, "website": church.website,
                       "denomination": church.denomination.label, "denomination_confidence": church.denomination.confidence},
            "features_to_research": {f: {"label": _feature(f)["label"], "allowed_values": _feature(f)["values"],
                                         "weight": next((p.weight for p in profile.preferences if p.feature == f), "")}
                                     for f in open_f},
            "profile_session": profile.session_id,
            "memory": __import__("app.memory", fromlist=["context"]).context(profile.session_id),
            "settled": settled,
            "open": open_f,
            "priority_open": priority_open,
            "user_questions": ctx.get("open_questions", []),
            "coverage": ctx.get("coverage", {}),
            "saved_sources": [{k: v for k, v in source.items() if k != "text"} for source in ctx.get("saved_sources", [])],
            "budget": _budget_remaining(ctx),
            "escalations": [e.model_dump(mode="json") for e in ctx.get("escalations", [])],
        },
        default=str,
    )


def _execute_tool(ctx: dict[str, Any], name: str, arguments: dict[str, Any]) -> dict:
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}"}
    args = dict(arguments)
    why = args.pop("why", "")
    ctx["_tool_why"] = why
    args.pop("church", None)   # never trust a model-supplied church object (R6)
    try:
        return fn(ctx, **args)
    except TypeError as e:     # missing/extra arguments from the model
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:     # R6: a failing tool returns an error to the model; it never kills the job
        import logging

        logging.getLogger("app.agent").warning("tool %s failed: %s", name, e, exc_info=True)
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def _open_dealbreakers(profile: PreferenceProfile, evidence) -> list[str]:
    dealbreakers = [p.feature for p in profile.preferences if p.weight == "dealbreaker"]
    settled, open_f = settled_open(evidence, dealbreakers)
    return [f for f in open_f if f in dealbreakers]


def _compact(messages: list[dict]) -> list[dict]:
    """R6: bound the context. Keep system + latest state; shrink tool results older than the last 2 turns."""
    state_idx = [i for i, m in enumerate(messages) if m.get("role") == "user" and str(m.get("content", "")).startswith("CURRENT STATE")]
    drop = set(state_idx[:-1])
    assistant_idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    cutoff = assistant_idx[-2] if len(assistant_idx) >= 2 else 0
    out = []
    for i, m in enumerate(messages):
        if i in drop:
            continue
        if m.get("role") == "tool" and i < cutoff and len(m.get("content") or "") > 600:
            m = {**m, "content": m["content"][:600] + "…[older result trimmed]"}
        out.append(m)
    return out


def deep_search(church: Church, profile: PreferenceProfile, job_id: str):
    """Run the deep-search agent loop. Never leaves the job 'running': errors end as status=error with a partial report."""
    try:
        db.get_job(job_id)
    except KeyError:
        db.create_job(job_id, profile.session_id, church.church_id)
    db.update_job(job_id, status="running", progress=0)

    ctx = tools.make_ctx(
        session_id=profile.session_id,
        church_id=church.church_id,
        job_id=job_id,
        profile=profile,
        church=church,
    )
    from app import qa
    ctx["saved_sources"] = db.research_sources(church.church_id)
    for source in ctx["saved_sources"]:
        tools._track_fetched(ctx, source["url"], source.get("text", ""))
        if source.get("kind") == "sermon":
            sid = "saved" + str(len(ctx["transcripts"]) + 1)
            ctx["transcripts"][sid] = {**source, "page_url": source["url"]}
    ctx["open_questions"] = [q["text"] for q in qa.open_questions(profile.session_id, church.church_id) if q["status"] == "open"]
    stale_unverified = []
    for source in ctx["saved_sources"]:
        if source.get("kind") == "sermon":
            continue  # Published transcripts are immutable; feed discovery adds new sermons.
        try:
            source_age = (datetime.now(timezone.utc) - datetime.fromisoformat(source["checked_at"]).replace(tzinfo=timezone.utc)).total_seconds() / 86400
        except (ValueError, TypeError, KeyError):
            source_age = 999
        if source_age >= 30:
            if tools.cancelled(ctx):
                return None
            if _budget_exhausted(ctx):
                stale_unverified.append(source["url"])
                continue
            ctx["_tool_why"] = "Verify previously saved public information older than one month"
            ctx["tool_calls"] += 1
            db.update_job(job_id, label="Verifying saved website information")
            verified_page = tools.fetch_page(ctx, source["url"])
            if not verified_page.get("text"):
                stale_unverified.append(source["url"])
    if stale_unverified:
        ctx["limitations"].append("Previously saved sources could not be verified: " + ", ".join(stale_unverified))
    previous = db.latest_job(church.church_id, "deep")
    if previous and previous.get("result_json") and ctx["saved_sources"]:
        verified = previous.get("verified_at") or previous.get("finished_at")
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(verified).replace(tzinfo=timezone.utc)).total_seconds() / 86400
            saved_result = json.loads(previous["result_json"])
        except (ValueError, TypeError):
            age, saved_result = 999, {}
        coverage = saved_result.get("coverage", {})
        if age < 30 and not stale_unverified and set(coverage) == set(tools.COVERAGE_AREAS):
            ctx["coverage"] = coverage
            ctx["resources"] = saved_result.get("resources", [])
            ctx["limitations"] = saved_result.get("limitations", [])
            ctx["sermons_analysed"] = saved_result.get("sermons_analysed", 0)
            ctx["reuse_verified_at"] = verified
            ready = all(v.get("status") in {"supported", "not_found"} for v in coverage.values()) and not ctx["limitations"]
            if ready:
                # Current user's questions must be answered, never copied from another report.
                answers = [qa.answer(profile.session_id, church.church_id, q, record=False) for q in ctx["open_questions"]]
                if all(a.get("confident") for a in answers):
                    ctx["finished"] = True
                    ctx["finish_summary"] = "Reused recently verified public research; report rebuilt for this conversation."
    status = "complete"
    try:
        if not ctx.get("sermons_analysed") and not tools.cancelled(ctx):
            db.update_job(job_id, label="Discovering sermon recordings")
            ctx["_tool_why"] = "Core deep-dive requirement: discover substantive sermon content"
            feeds = tools.find_sermon_feeds(ctx).get("feeds", [])
            for feed in sorted(feeds, key=lambda f: f.get("kind") != "youtube"):
                if tools.cancelled(ctx) or _budget_exhausted(ctx):
                    break
                if feed.get("kind") != "youtube":
                    continue
                ctx["_tool_why"] = "Find recent public service recordings for sermon analysis"
                ctx["tool_calls"] += 1
                listing = tools.get_sermons(ctx, feed["url"], limit=5).get("sermons", [])
                ids = [x["sermon_id"] for x in listing]
                if not ids:
                    continue
                db.update_job(job_id, label="Collecting sermon transcripts")
                ctx["_tool_why"] = "Collect published captions or audio transcripts for substantive analysis"
                ctx["tool_calls"] += 1
                tools.transcribe_sermons(ctx, ids)
                if ctx.get("transcripts"):
                    db.update_job(job_id, label="Analysing sermon teaching")
                    ctx["_tool_why"] = "Analyse actual teaching, rather than titles or stated values"
                    ctx["tool_calls"] += 1
                    tools.analyse_sermons(ctx)
                    break
        _loop(church, profile, job_id, ctx)
    except Exception as e:  # R6: model/API failure -> partial report, job marked error
        import logging

        logging.getLogger("app.agent").exception("deep search %s failed", job_id)
        status = "error"
        tools.escalate(ctx, reason="budget_exhausted", message=f"Research stopped early because of an error ({type(e).__name__}). "
                       "What we found so far is below.", questions=[])

    if tools.cancelled(ctx):
        return None
    if not ctx.get("sermons_analysed"):
        ctx["limitations"].append("Sermon analysis incomplete: no substantive sermon transcripts were analysed.")
    all_evidence = db.get_evidence(church.church_id)
    updated_church = ctx.get("church") or church
    updated_church = updated_church.model_copy(update={"evidence": all_evidence, "stage_done": max(church.stage_done, 3)})
    try:
        rep, narrative = report_module.build_report(updated_church, profile, ctx)
        if tools.cancelled(ctx):
            return None
        path = report_module.save_report(rep, job_id, narrative=narrative)
    except Exception as e:
        db.update_job(job_id, status="error", progress=100, finished_at=datetime.now(timezone.utc).isoformat())
        raise RuntimeError(f"report failed: {e}") from e
    db.update_job(job_id, status=status, progress=100, report_path=str(path), verified_at=ctx.get("reuse_verified_at") if not ctx.get("sources_refreshed") else datetime.now(timezone.utc).isoformat(), result_json=json.dumps({"coverage": ctx["coverage"], "resources": ctx["resources"], "limitations": ctx["limitations"], "sermons_analysed": ctx["sermons_analysed"]}), label="Report ready" if not ctx["limitations"] and status == "complete" else "Stopped early — partial report ready",
                  finished_at=datetime.now(timezone.utc).isoformat())
    return rep


def _loop(church: Church, profile: PreferenceProfile, job_id: str, ctx: dict[str, Any]) -> None:
    if church.evidence:
        db.add_evidence(church.church_id, church.evidence)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": load_prompt("deep_search.v5")},
        {"role": "user", "content": _state_message(church, profile, ctx) + "\nUser questions to research: " + json.dumps(ctx.get("open_questions", []))},
    ]
    nudged = False
    while True:
        if tools.cancelled(ctx):
            return
        evidence = db.get_evidence(church.church_id)
        if ctx.get("finished"):
            break
        if _budget_exhausted(ctx):
            ctx["limitations"].append("Research budget reached; remaining coverage and questions may be incomplete.")
            open_db = _open_dealbreakers(profile, evidence)
            if open_db:
                from app.features import feature as _feature

                tools.escalate(
                    ctx,
                    reason="budget_exhausted",
                    message="Research budget reached with must-have features still open.",
                    questions=[f"Ask the church: {_feature(fid)['label']}?" for fid in open_db[:4]],
                )
            break

        db.update_job(job_id, label="Choosing the next research source", done=len(ctx.get("transcripts", {})), total=0)
        response = chat_tools("deep_search", _compact(messages), _TOOL_SCHEMAS, tier="strong", tool_choice="required")
        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            if nudged:
                ctx["limitations"].append("Research stopped because the agent did not continue using research tools.")
                break
            nudged = True   # one plain-text reply: nudge once, then stop
            messages.append({"role": "assistant", "content": response.get("content") or ""})
            messages.append({"role": "user", "content": "Use a tool, or call finish if you are done."})
            continue

        messages.append({"role": "assistant", "content": response.get("content") or "", "tool_calls": tool_calls})
        for tc in tool_calls:   # R6: every tool_call gets its tool message, in order, with nothing in between
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = None
            ctx["tool_calls"] = int(ctx.get("tool_calls", 0)) + 1
            if args is None:
                result = {"error": "arguments were not valid JSON; try again with smaller arguments"}
            elif tools.cancelled(ctx):
                result = {"cancelled": True}
            elif ctx.get("finished"):
                result = {"skipped": "finish was already called"}
            else:
                db.update_job(job_id, label={"fetch_page": "Reading public website pages", "transcribe_sermons": "Collecting sermon transcripts", "analyse_sermons": "Analysing sermon teaching", "search_web": "Checking public sources"}.get(name, "Researching " + name.replace("_", " ")), done=len(ctx.get("transcripts", {})), total=0)
                result = _execute_tool(ctx, name, args)
            messages.append({"role": "tool", "tool_call_id": tc.get("id", name), "content": json.dumps(result, default=str)})

        messages.append({"role": "user", "content": _state_message(ctx["church"], profile, ctx)})
        db.update_job(job_id, progress=min(95.0, ctx.get("tool_calls", 0) * 100.0 / max(1, get_settings().deep_max_tool_calls)))


def run_deep_search_job(
    church: Church,
    profile: PreferenceProfile,
    job_id: str,
    *,
    background: bool = False,
):
    """Start deep search in a thread or run synchronously (tests)."""
    if background:
        def _run() -> None:
            try:
                deep_search(church, profile, job_id)
            except Exception:   # last resort: never leave the job "running" (R6)
                import logging

                logging.getLogger("app.agent").exception("deep search job %s crashed", job_id)
                try:
                    db.update_job(job_id, status="error", finished_at=datetime.now(timezone.utc).isoformat())
                except Exception:
                    pass

        _executor.submit(_run)
        return None
    return deep_search(church, profile, job_id)
