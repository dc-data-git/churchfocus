"""Stage 3 deep-search agent loop (INTERFACES §4, ARCHITECTURE §8)."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from app import db
from app.config import get_settings
from app.features import settled_open
from app.llm import chat_tools, load_prompt
from app.models import Church, PreferenceProfile
from app.stage3 import report as report_module
from app.stage3 import tools

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="deep-search")

_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "Fetch a public church page",
            "parameters": {
                "type": "object",
                "required": ["url", "why"],
                "properties": {"url": {"type": "string"}, "why": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the public web",
            "parameters": {
                "type": "object",
                "required": ["query", "why"],
                "properties": {"query": {"type": "string"}, "why": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wayback_snapshots",
            "description": "List Wayback Machine snapshots for a URL",
            "parameters": {
                "type": "object",
                "required": ["url", "years", "why"],
                "properties": {
                    "url": {"type": "string"},
                    "years": {"type": "array", "items": {"type": "integer"}},
                    "why": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "denomination_lookup",
            "description": "Look up a denomination in the KB",
            "parameters": {
                "type": "object",
                "required": ["name_or_id", "why"],
                "properties": {"name_or_id": {"type": "string"}, "why": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "denomination_locator_search",
            "description": "Search official denomination locators",
            "parameters": {
                "type": "object",
                "required": ["church_name", "city", "why"],
                "properties": {
                    "church_name": {"type": "string"},
                    "city": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_sermon_feeds",
            "description": "Find sermon/media feeds on the church website",
            "parameters": {
                "type": "object",
                "required": ["church", "why"],
                "properties": {"church": {"type": "object"}, "why": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sermons",
            "description": "List sermons from a feed",
            "parameters": {
                "type": "object",
                "required": ["feed_url", "limit", "why"],
                "properties": {
                    "feed_url": {"type": "string"},
                    "limit": {"type": "integer"},
                    "why": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transcribe_sermon",
            "description": "Transcribe one sermon item",
            "parameters": {
                "type": "object",
                "required": ["item", "why"],
                "properties": {"item": {"type": "object"}, "why": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyse_sermons",
            "description": "Analyse sermon transcripts for observed features",
            "parameters": {
                "type": "object",
                "required": ["texts", "features", "why"],
                "properties": {
                    "texts": {"type": "array", "items": {"type": "object"}},
                    "features": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_evidence",
            "description": "Store verified evidence for this church",
            "parameters": {
                "type": "object",
                "required": ["evidence", "why"],
                "properties": {
                    "evidence": {"type": "array", "items": {"type": "object"}},
                    "why": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate",
            "description": "Flag a concern for the user",
            "parameters": {
                "type": "object",
                "required": ["reason", "message", "questions", "why"],
                "properties": {
                    "reason": {"type": "string"},
                    "message": {"type": "string"},
                    "questions": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Stop research with a short summary",
            "parameters": {
                "type": "object",
                "required": ["summary", "why"],
                "properties": {"summary": {"type": "string"}, "why": {"type": "string"}},
            },
        },
    },
]

_TOOL_DISPATCH: dict[str, Callable[..., dict]] = {
    "fetch_page": tools.fetch_page,
    "search_web": tools.search_web,
    "wayback_snapshots": tools.wayback_snapshots,
    "denomination_lookup": tools.denomination_lookup,
    "denomination_locator_search": tools.denomination_locator_search,
    "find_sermon_feeds": tools.find_sermon_feeds,
    "get_sermons": tools.get_sermons,
    "transcribe_sermon": tools.transcribe_sermon,
    "analyse_sermons": tools.analyse_sermons,
    "record_evidence": tools.record_evidence,
    "escalate": tools.escalate,
    "finish": tools.finish,
}


def _priority_features(profile: PreferenceProfile) -> list[str]:
    return [p.feature for p in profile.preferences if p.weight in ("dealbreaker", "important")]


def _target_features(profile: PreferenceProfile) -> list[str]:
    return [p.feature for p in profile.preferences if p.weight != "dont_care"]


def _important_settled(profile: PreferenceProfile, evidence) -> bool:
    feature_ids = _priority_features(profile)
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
    return json.dumps(
        {
            "church": church.model_dump(mode="json"),
            "profile_session": profile.session_id,
            "settled": settled,
            "open": open_f,
            "priority_open": priority_open,
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
    if name == "find_sermon_feeds" and "church" not in args and ctx.get("church") is not None:
        args["church"] = ctx["church"]
    return fn(ctx, **args)


def _open_dealbreakers(profile: PreferenceProfile, evidence) -> list[str]:
    dealbreakers = [p.feature for p in profile.preferences if p.weight == "dealbreaker"]
    settled, open_f = settled_open(evidence, dealbreakers)
    return [f for f in open_f if f in dealbreakers]


def deep_search(church: Church, profile: PreferenceProfile, job_id: str):
    """Run the deep-search agent loop synchronously (tests and inline runs)."""
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
    if church.evidence:
        db.add_evidence(church.church_id, church.evidence)

    system = load_prompt("deep_search.v1")
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": _state_message(church, profile, ctx)},
    ]

    while True:
        evidence = db.get_evidence(church.church_id)
        if ctx.get("finished") or _important_settled(profile, evidence):
            break
        if _budget_exhausted(ctx):
            open_db = _open_dealbreakers(profile, evidence)
            if open_db:
                tools.escalate(
                    ctx,
                    reason="budget_exhausted",
                    message="Research budget reached with must-have features still open.",
                    questions=[f"What is your position on {fid}?" for fid in open_db[:4]],
                )
            break

        response = chat_tools("deep_search", messages, _TOOL_SCHEMAS, tier="strong")
        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            break

        messages.append({"role": "assistant", "content": response.get("content") or "", "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            ctx["tool_calls"] = int(ctx.get("tool_calls", 0)) + 1
            result = _execute_tool(ctx, name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", name),
                    "content": json.dumps(result, default=str),
                }
            )
            if name == "record_evidence" and result.get("ok"):
                messages.append(
                    {
                        "role": "user",
                        "content": "Updated settled/open:\n" + json.dumps(
                            {"settled": result.get("settled"), "open": result.get("open")}
                        ),
                    }
                )
            if ctx.get("finished"):
                break

        messages.append({"role": "user", "content": _state_message(ctx["church"], profile, ctx)})
        db.update_job(job_id, progress=min(95.0, ctx.get("tool_calls", 0) * 2))

    all_evidence = db.get_evidence(church.church_id)
    updated_church = church.model_copy(update={"evidence": all_evidence, "stage_done": max(church.stage_done, 3)})
    rep, narrative = report_module.build_report(updated_church, profile, ctx)
    path = report_module.save_report(rep, job_id, narrative=narrative)
    db.update_job(
        job_id,
        status="complete",
        progress=100,
        report_path=str(path),
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    return rep


def run_deep_search_job(
    church: Church,
    profile: PreferenceProfile,
    job_id: str,
    *,
    background: bool = False,
):
    """Start deep search in a thread or run synchronously (tests)."""
    if background:
        _executor.submit(deep_search, church, profile, job_id)
        return None
    return deep_search(church, profile, job_id)
