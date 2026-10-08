"""v2 conversation (REDESIGN §8, D26-D44). One strong-model call per turn, in parallel with the crisis check.

The conversation is free-form; the back end stays formulaic: the model only proposes memory ops, the server validates
them against features.yaml (memory.append) and the ranking reads memory.to_profile() with fixed rules.
"""
from __future__ import annotations

import json
import logging
import threading
import re
from collections import defaultdict
_turn_locks = defaultdict(threading.RLock)
from concurrent.futures import ThreadPoolExecutor

from app import chat, db, lexicon, llm, memory
from app.features import all_features
from app.models import MemoryOp, StepLog

log = logging.getLogger("app.conversation")
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="conv-bg")
DEFAULT_RADIUS = 15.0
GREETING = ("Hi! I'm ChurchFocus. I can help you find a church that fits — for you or for someone you're helping. "
            "Where are you starting from? A town, ZIP code, or a place like a college works.")
GREETING_OPTIONS = ["I'm new in town", "I'm helping someone else look"]

TURN_SCHEMA = {
    "type": "object",
    "required": ["reply"],
    "properties": {
        "reply": {"type": "string"},
        "memory_ops": {"type": "array", "items": MemoryOp.model_json_schema()},
        "intent": {"type": "string"},
        "church_refs": {"type": "array", "items": {"type": "string"}},
        "church_ref": {"type": ["string", "null"]},
        "options": {"type": ["array", "null"], "items": {"type": "string"}},
        "denomination": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
        "location": {"type": ["object", "null"], "properties": {"text": {"type": "string"}, "limit_miles": {"type": ["number", "null"]}}},
    },
}
DENOM_SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}


# ---------------------------------------------------------------- context for the model
_features_text: str | None = None
_denoms_text: str | None = None


def _features_catalog() -> str:
    global _features_text
    if _features_text is None:
        lines = []
        for fid, f in all_features().items():
            vals = "|".join(str(v) for v in f.get("values", []))
            lines.append(f"{fid}: {f.get('label', fid)} [{vals}]" + (" (sensitive)" if f.get("sensitive") else ""))
        _features_text = "\n".join(lines)
    return _features_text


def _denoms_catalog(limit: int = 120) -> str:
    global _denoms_text
    if _denoms_text is None:
        from app.denom.kb import get_kb
        kb = get_kb()
        gids = [g for g in kb.groups if kb.is_christian(g)]
        gids.sort(key=lambda g: -(kb.groups[g]["census_2020"].get("adherents") or 0))
        _denoms_text = "\n".join(f"{g}: {kb.groups[g]['name']}" for g in gids[:limit])
    return _denoms_text


def _churches_context(sid: str, n: int = 150) -> str:
    from app.stage1 import search
    try:
        rows = search.ranked(sid)[:n]
    except Exception:
        return "(none yet)"
    if not rows:
        return "(none yet)"
    out = []
    for ch, mr in rows:
        facts = "; ".join(f"{e.feature}={e.value}" for e in ch.evidence if e.tier in ("A", "B"))[:300]
        out.append(f"{ch.church_id} | {ch.name} | {ch.denomination.label} | {ch.distance_miles:.1f} mi | fit {mr.fit}"
                   + (f" | facts: {facts}" if facts else ""))
    return "\n".join(out)


def _memory_context(sid: str) -> str:
    items = memory.plain_summary(sid)
    if not items:
        return "(empty)"
    cur = memory.current(sid)
    lines = []
    for it in items:
        op = cur.get(it["key"])
        lines.append(f"turn {op.t if op else '?'} | {it['key']} | {it['text']} | src={it['src']} conf={it['conf']:.2f}")
    return "\n".join(lines)


def _system_prompt(sid: str, text: str) -> str:
    hints = lexicon.hints(text or "")
    hint_text = "\n".join(f"- \"{h['term']}\": {h.get('gloss', '')} → {json.dumps(h.get('features'))}" for h in hints) or "(none)"
    loc = memory.location(sid)
    return "\n\n".join([
        llm.load_prompt("interview_skill.v8"),
        "## ABOUT YOU: current view and historical corrections/reasons\n" + json.dumps(memory.context(sid),ensure_ascii=False),
        "## RESEARCH STATE\n" + json.dumps(db.session_state(sid)),
        "## STARTING PLACE\n" + (json.dumps({k: loc.get(k) for k in ("text", "limit_miles")}) if loc else "(not given yet)"),
        "## LEXICON HINTS for this message (guesses only; conf ≤ 0.5)\n" + hint_text,
        "## CHURCHES (best fits so far; use these ids for church_ref)\n" + _churches_context(sid),
        "## DENOMINATIONS (id: name)\n" + _denoms_catalog(),
        "## FEATURES (id: label [allowed values])\n" + _features_catalog(),
    ])


def _history(sid: str, n: int = 16) -> list[dict]:
    msgs = [m for m in chat.since(sid, 0) if m["role"] in ("user", "bot")][-n:]
    return [{"role": "user" if m["role"] == "user" else "assistant", "content": m["text"]} for m in msgs]


# ---------------------------------------------------------------- crisis
def _crisis(text: str) -> bool:
    from app.stage0.interviewer import CRISIS_KEYWORDS, CRISIS_SCHEMA
    if CRISIS_KEYWORDS.search(text or ""):
        return True
    try:
        out = llm.complete_json("crisis_check", [{"role": "system", "content": llm.load_prompt("crisis_check.v2")},
                                                 {"role": "user", "content": text}], CRISIS_SCHEMA, tier="fast")
        return bool(out.get("escalate"))
    except Exception:
        return False


# ---------------------------------------------------------------- location + search
def _set_location(sid: str, t: int, loc: dict, ev: str) -> str | None:
    """Geocode + store. Returns an error message for the person, or None. Starts the search in the background."""
    from app.stage1 import places, search

    text = (loc.get("text") or "").strip()
    limit = loc.get("limit_miles")
    try:
        limit = min(max(float(limit),1), 50.0) if limit else None
    except (TypeError, ValueError):
        limit = None
    old = memory.location(sid) or {}
    same_place = text and old.get("text") and places.clean_origin(text).lower() == places.clean_origin(old["text"]).lower()
    if not text and old:
        text, same_place = old["text"], True
    if not text:
        return None
    if same_place:
        lat, lng = old["lat"], old["lng"]
        if limit is None:
            limit = old.get("limit_miles")
    else:
        try:
            lat, lng = places.geocode(text)
            same_place = bool(old) and search._miles(lat,lng,old.get("lat",0),old.get("lng",0)) < 0.5
            if same_place and limit is None:
                limit = old.get("limit_miles")
        except Exception as e:
            log.warning("geocode failed for %r: %s", text, e)
            return f"I couldn't find \"{places.clean_origin(text)}\" on the map. Could you give me a town and state, or a ZIP code?"
    if same_place and (limit or None) == (old.get("limit_miles") or None):
        return None
    val = {"text": places.clean_origin(text), "lat": lat, "lng": lng, "limit_miles": limit}
    memory.append(sid, [MemoryOp(t=t, key="location", val=val, stance="want", strength=1.0, conf=0.95, src="stated",
                                 ev=ev[:200], why="new starting place" if not same_place else "changed distance")])
    if not same_place:
        search.reset(sid)
        _pool.submit(_first_search, sid, lat, lng, limit or DEFAULT_RADIUS, val["text"])
    else:
        search.request_coverage(sid,lat,lng,limit or DEFAULT_RADIUS)
    return None


def _first_search(sid: str, lat: float, lng: float, radius: float, place: str) -> None:
    """Discovery updates the sidebar silently. Research waits for explicit user action."""
    from app.stage1 import search
    current_location=memory.location(sid)
    if current_location and search._miles(lat,lng,current_location["lat"],current_location["lng"]) < 0.5:
        search.request_coverage(sid,lat,lng,radius)


# ---------------------------------------------------------------- intents
def _church_id(sid: str, ref) -> str | None:
    from app.stage1 import search
    if not ref:
        return None
    ref=str(ref)
    if search.get_church(sid,ref):
        return ref
    def normalize(value):
        value=re.sub(r"\bpres\b", "presbyterian", value.casefold())
        return re.sub(r"[^\w ]+", " ", value).split()
    wanted=normalize(ref)
    hits=[ch for ch in search.churches(sid) if wanted and all(word in normalize(ch.name) for word in wanted)]
    return hits[0].church_id if len(hits)==1 else None


def _denomination_answer(question: str, ref: str | list[str] | None) -> str:
    from app.denom.kb import get_kb
    kb = get_kb()
    if isinstance(ref, list):
        ids = []
        for name in ref[:2]:
            hits = kb.find(str(name), k=1)
            gid = name if name in kb.groups else (hits[0]["id"] if hits else None)
            if gid and kb.is_christian(gid):
                ids.append(gid)
        if len(ids) == 2:
            facts = [kb.get(gid) for gid in ids]
            comparison = kb.compare(ids[0], ids[1], [f for f in ("theology.scripture", "baptism.mode", "baptism.subject", "women.senior_pastor", "communion.frequency") if f in all_features()])
            out = llm.complete_json("denom_answer", [
                {"role":"system", "content":"Compare these denominations using ONLY the supplied facts. Describe typical denomination positions, say congregations vary, and include source URLs from the facts. Return JSON {answer: string}."},
                {"role":"user", "content":json.dumps({"question":question,"groups":facts,"comparison":comparison})[:18000]}], DENOM_SCHEMA, tier="fast")
            return out["answer"]
        ref = ref[0] if ref else None
    gid = ref if ref in kb.groups else None
    if not gid and ref:
        hits = kb.find(ref, k=1)
        gid = hits[0]["id"] if hits else None
    if not gid or not kb.is_christian(gid):
        return "I don't have a summary for that group. I can tell you about most Christian denominations — which one?"
    info = kb.get(gid)
    try:
        out = llm.complete_json("denom_answer", [
            {"role": "system", "content": "You summarize a Christian denomination for someone looking for a church. Use ONLY the "
             "facts in the JSON given. Neutral and descriptive; never say which beliefs are right. 2-4 short sentences. "
             "Say plainly what isn't in the facts, and that individual congregations vary. Return JSON {\"answer\": str}."},
            {"role": "user", "content": f"Question: {question}\nFacts: {json.dumps(info)[:6000]}"}], DENOM_SCHEMA, tier="fast")
        return out["answer"]
    except Exception:
        return f"{info['name']} is in the {info.get('tradition') or 'Christian'} tradition. Individual congregations vary."


def _apply_ops(sid: str, t: int, raw_ops: list, *, text: str = "") -> list[str]:
    ops, dropped = [], []
    for o in raw_ops or []:
        if not isinstance(o, dict) or o.get("key") == "location":
            continue
        try:
            o = {**o, "t": t}
            if o.get("op") == "add":
                o["op"] = "assert"  # Preserve valid preferences from the observed legacy model synonym.
            if o.get("op") == "revise" and not o.get("supersedes"):
                o["supersedes"] = None
            evidence = str(o.get("ev") or "").casefold()
            source_text = text.casefold() or evidence
            if o.get("src", "stated") != "user_edit":
                mandatory = re.search(r"\b(must|required|require|non.negotiable|only|never|wouldn.t be comfortable|not comfortable|rule out|exclude)\b", evidence)
                if not mandatory:
                    o["strength"] = min(float(o.get("strength",0.6)), 0.7)
            if o.get("key") in {"denomination", "identity.denomination"}:
                specific = re.search(r"missouri|wisconsin|synod|episcopal|\blcms\b|\bwels\b|\bels\b|\bacna\b", evidence)
                families = []
                if not specific:
                    if "lutheran" in evidence: families.append("lutheran")
                    if "anglican" in evidence: families.append("anglican_episcopal")
                if families:
                    o.update(key="identity.tradition", val=families)
                elif "catholic" in evidence and o.get("stance") == "avoid":
                    o.update(key="identity.branch", val="catholic")
            if o.get("key") == "identity.branch" and o.get("stance") == "want" and "protestant" in source_text and "mainline" not in source_text and "evangelical" not in source_text:
                o.update(val=["mainline_protestant","evangelical_protestant","black_protestant","pentecostal_charismatic","restorationist","anabaptist"])
            if o.get("key") == "theology.scripture" and "biblical authority" in evidence and not re.search(r"inerran|without error",evidence):
                o.update(val=["inerrant","infallible","inspired_authoritative"])
            if o.get("key") == "worship.style" and o.get("val") == "traditional_hymns":
                o.update(key="worship.music_sources",val="hymns")
            if o.get("key") == "women.senior_pastor" and o.get("stance") == "avoid" and o.get("val") == "no" and re.search(r"not (?:as )?(?:head|lead|senior) pastors?|not .*priests?|except .*pastors?", source_text):
                o.update(stance="want",val="no")
            ops.append(MemoryOp.model_validate(o))
        except Exception as e:
            dropped.append(f"bad op {o!r}: {e}"[:200])
    if re.search(r"\b(?:definitely|only|must be|strictly) protestant\b", text, re.I):
        ops = [o for o in ops if o.key != "identity.branch"]
        branches = ["mainline_protestant", "evangelical_protestant", "black_protestant", "pentecostal_charismatic", "restorationist", "anabaptist"]
        ops.extend([
            MemoryOp(t=t,op="revise",key="identity.branch",val=branches,stance="want",strength=.9,conf=1,src="stated",ev=text[:200]),
            MemoryOp(t=t,op="revise",key="identity.branch",val=["catholic","eastern_orthodox","oriental_orthodox"],stance="avoid",strength=.9,conf=1,src="stated",ev=text[:200]),
        ])
    return dropped + memory.append(sid, ops)


def turn(session_id: str, text: str | None) -> dict:
    with _turn_locks[session_id]:
        return _turn(session_id,text)


def _turn(session_id: str, text: str | None) -> dict:
    sid = session_id
    start_n = max((m["n"] for m in chat.since(sid, 0)), default=0)
    if text is None or not str(text).strip():
        if start_n == 0:
            chat.post(sid, GREETING, {"options": GREETING_OPTIONS})
        return {"messages": chat.since(sid, start_n)}
    text = str(text).strip()[:2000]
    chat.post(sid, text, role="user")
    t = memory.next_turn(sid)
    choice=text.casefold().strip().rstrip(".!?")
    if choice in ("start a new chat","new chat"):
        result=restart(sid,"new")
        return {"session_id":result["session_id"],"messages":chat.since(result["session_id"],0)}
    if choice in ("change this search here","continue in this window"):
        restart(sid,"continue")
        return {"messages":chat.since(sid,start_n)}
    if re.search(r"\b(start over|start again|reset the search|fresh start)\b",text,re.I):
        db.state_update(sid,restart_pending=True,pending_location=None)
        chat.post(sid,"Would you like to start a new chat, or change this search here?",{"options":["Start a new chat","Change this search here"]})
        return {"messages":chat.since(sid,start_n)}

    msgs = [{"role": "system", "content": _system_prompt(sid, text)}] + _history(sid)
    crisis_f = _pool.submit(_crisis, text)
    try:
        out = llm.complete_json("interview_turn", msgs, TURN_SCHEMA, tier="strong")
    except Exception as e:
        log.exception("interview turn failed")
        out = {"reply": "Sorry — I had trouble with that one. Could you say it again?", "intent": "chat"}
    if crisis_f.result():
        from app.stage0.interviewer import CRISIS_MESSAGE
        chat.post(sid, CRISIS_MESSAGE, {"kind": "crisis"})
        return {"messages": chat.since(sid, start_n)}

    # Starting over is a choice; don't silently apply a model-proposed destructive reset.
    if out.get("intent")=="new_search":
        db.state_update(sid,restart_pending=True,pending_location=out.get("location"))
        chat.post(sid,"Would you like to start a new chat, or change this search here?",{"options":["Start a new chat","Change this search here"]})
        return {"messages":chat.since(sid,start_n)}
    if re.search(r"help(?:ing)? (?:my|our|a|someone|their).*?(?:in-laws|in laws|parents|friend|find|church)",text,re.I):
        memory.append(sid,[MemoryOp(t=t,key="for_whom",val="other",ev=text[:200],why="Explicitly helping someone else",strength=0.6,conf=1,src="stated")])
    dropped = _apply_ops(sid, t, out.get("memory_ops"),text=text)
    if re.search(r"prioriti[sz]e.*liturgical",text,re.I):
        memory.append(sid,[MemoryOp(t=t,key="worship.style",val="liturgical_traditional",stance="want",strength=.7,conf=1,src="stated",ev=text[:200],why="Explicit priority for liturgical worship")])
    reply = out.get("reply") or ""
    options = out.get("options") if isinstance(out.get("options"), list) else None
    intent = out.get("intent") or "chat"

    loc = out.get("location") if isinstance(out.get("location"), dict) else None
    if not loc:
        loc = next((o["val"] for o in reversed(out.get("memory_ops") or [])
                    if isinstance(o, dict) and o.get("key") == "location" and isinstance(o.get("val"), dict)), None)
    if loc:
        err = _set_location(sid, t, loc, text)
        if err:
            reply, options = err, None

    refs = [r for r in (out.get("church_refs") or []) if r] + ([out["church_ref"]] if out.get("church_ref") else [])
    cids = list(dict.fromkeys(c for c in (_church_id(sid, r) for r in refs) if c))
    requested_deep = bool(re.search(r"\bdeep[ -]?dive\b",text,re.I))
    if requested_deep:
        intent="find_these_out"
        raw_ref=re.split(r"\bdeep[ -]?dive(?: on| into| for)?\s*",text,flags=re.I)[-1].strip(" .!?")
        raw_id=_church_id(sid,raw_ref)
        if raw_id:
            cids=[raw_id]
        elif raw_ref and raw_ref.casefold() not in ("it","that church","this church"):
            cids=[]
    if not cids and intent=="church_question" and db.session_state(sid).get("active_church"):
        active=db.session_state(sid)["active_church"]
        if _church_id(sid,active):
            cids=[active]
    urls=re.findall(r"https?://[^\s<>]+",text)
    if urls:
        from app.stage1 import search
        from app import jobs
        try:
            church=search.add_url(sid,urls[0].rstrip(".,)"))
            jobs.pin(sid,church.church_id,True)
            cids=[church.church_id]
            reply=f"I've added and pinned {church.name}. I'm checking its public website for factual information."
            intent="find_these_out" if requested_deep else "url_added"
        except Exception:
            log.exception("supplied church URL failed")
            reply="I couldn't open that public website. Please check the link and try again."
            intent="url_error"
    if cids:
        db.state_update(sid,active_church=cids[0])
    if intent == "church_question" and cids:
        from app import qa
        try:
            answers = []
            for cid in dict.fromkeys(cids[:3]):
                a = qa.answer(sid, cid, text)
                from app.stage1 import search
                ch = search.get_church(sid, cid)
                heading = (ch.name + ":\n") if len(cids) > 1 and ch else ""
                answers.append(heading + a["answer"] + "".join("\n" + source["url"] for source in a.get("sources", [])))
            reply = "\n\n".join(answers)
        except Exception:
            log.exception("qa failed")
    elif intent == "denomination_question":
        try:
            reply = _denomination_answer(text, out.get("denomination"))
        except Exception:
            log.exception("denomination answer failed")
            reply = "I couldn’t read that comparison just now. Please try again."
    elif intent == "know_more":
        from app import jobs
        if cids:
            know_more(sid,cids[:5],post=False)
            reply="Your selected churches are pinned. Their factual summaries will appear in the list. What would you like to ask about them?"
        else:
            jobs.prepare(sid)
            reply="Select 1–5 churches in the Churches tab, then choose Research selected."
    elif intent == "find_these_out":
        from app import jobs
        if not cids:
            reply="Which church should I research? Please use its full name from the list, or its Deep dive button."
        else:
            try:
                jid=jobs.submit(sid,cids[0],"deep",questions=[text])
                reply="The deep dive is running. I'll research the full picture, including sermons and your questions; its progress stays visible above the message box."
            except Exception:
                log.exception("deep launch failed")
                reply="I couldn't start that deep dive. Please try its Deep dive button again."
    elif intent == "church_question" and not cids:
        reply="Which church do you mean? Please give its full name from the list."
    elif intent == "search_now" and not memory.location(sid):
        reply = reply or "Happy to — where should I search from?"

    chat.post(sid, reply, {"options": options[:3]} if options else None)
    try:
        from app import log as steplog
        steplog.write_step(StepLog(session_id=sid, stage=0, step=t, action="interview_turn", why=intent,
                                   input={"text": text[:300]},
                                   result_summary=json.dumps({"ops": out.get("memory_ops"), "dropped": dropped})[:4000]))
    except Exception:
        pass
    return {"messages": chat.since(sid, start_n)}


def know_more(session_id: str, church_ids: list[str], post: bool = True) -> dict:
    from app import jobs
    result=jobs.select(session_id,church_ids)
    if post:
        chat.post(session_id,"Your selected churches are pinned. Their factual summaries will appear in the list. What would you like to ask about them?",{"kind":"know_more"})
    return result


def restart(session_id: str, mode: str) -> dict:
    from app.stage1 import search
    import uuid
    if mode not in ("new","continue"):
        raise ValueError("Choose new or continue")
    pending=db.session_state(session_id).get("pending_location")
    search.reset(session_id)
    if mode=="new":
        sid=uuid.uuid4().hex
        turn(sid,None)
    else:
        sid=session_id
        memory.append(sid,[MemoryOp(t=memory.next_turn(sid),op="retract",key="location",src="user_edit",why="starting another search in this chat")])
        if pending:
            err=_set_location(sid,memory.next_turn(sid),pending,"confirmed change to this search")
            reply=err or "I've updated the starting place. The nearby list is updating; your preferences are still available."
        else:
            reply="We'll continue here. Where would you like to start this search?"
        chat.post(sid,reply)
    return {"session_id":sid,"research":db.session_state(sid)}


EDIT_SCHEMA = {"type": "object", "required": ["memory_ops"], "properties": {"memory_ops": {"type": "array", "items": MemoryOp.model_json_schema()}}}


def apply_user_edit(session_id: str, key: str, text: str) -> list[str]:
    """The person rewrote an About-you item. Empty text = remove it. Otherwise interpret the text into ops (src=user_edit)."""
    t = memory.next_turn(session_id)
    text = (text or "").strip()
    if not text:
        memory.append(session_id, [MemoryOp(t=t, op="retract", key=key, src="user_edit", why="removed in About you")])
        return []
    if key == "location":
        return [e for e in [_set_location(session_id, t, {"text": text}, text)] if e]
    try:
        out = llm.complete_json("memory_edit", [
            {"role": "system", "content": "Turn the person's edit of one remembered item into memory ops (same op format as the "
             "interview skill). Use only feature ids and values from FEATURES. Return JSON {\"memory_ops\": [...]}.\n\n"
             "## FEATURES\n" + _features_catalog() + "\n\n## DENOMINATIONS\n" + _denoms_catalog()},
            {"role": "user", "content": f"Item key: {key}\nNew text: {text}"}], EDIT_SCHEMA, tier="fast")
    except Exception:
        return ["could not interpret the edit"]
    ops = [{**o, "src": "user_edit", "conf": max(float(o.get("conf", 0.9)), 0.9)} for o in out.get("memory_ops", []) if isinstance(o, dict) and o.get("key")==key]
    return _apply_ops(session_id, t, ops)
