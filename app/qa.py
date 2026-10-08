"""v2 church Q&A from what was read + Open questions (REDESIGN §7, D44).

Retrieve relevant saved public documents and sermon text, then bounded targeted resources when needed.
Exact source excerpts are required. Unsupported answers become open questions; no deep job is created.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from app import db, llm

QA_SCHEMA = {"type": "object", "required": ["answer", "confident", "sources"],
             "properties": {"answer": {"type": "string"}, "confident": {"type": "boolean"}, "sources": {"type": "array"}}}
QA_SYSTEM = ("You answer a factual question about one church using ONLY the FACTS and PAGES given (text from the church's "
             "public website and sermons). ABOUT_YOU supplies conversational history/preferences, never church facts. "
             "Respect corrections, never invent preferences. Never guess or use outside knowledge. Never judge beliefs. "
             "Only attribute passages clearly about the named church. Other churches or organizations in resource lists "
             "are not its staff, services, or ministries. Describe external resources explicitly as external when asked. "
             "If the material answers it, reply in 1-3 friendly sentences and give 1-2 sources, each with the url and a "
             "quote copied EXACTLY from the page text. If it does not clearly answer it, set confident=false and say "
             "briefly what you did find, if anything. Return JSON {\"answer\": str, \"confident\": bool, "
             "\"sources\": [{\"url\": str, \"quote\": str}]}.")


def _retained(timestamp, days: int) -> bool:
    try:
        dt = timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(str(timestamp))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= datetime.now(timezone.utc) - timedelta(days=days)
    except (TypeError, ValueError):
        return False


def _retained_job(job: dict | None, kind: str) -> bool:
    return bool(job and _retained(job.get("finished_at") or job.get("started_at"), 90 if kind == "medium" else 365))


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in
            {"the", "and", "what", "does", "their", "church", "about", "tell", "have", "can"}}


def _pages(church_id: str, max_chars: int = 18000, question: str = "") -> list[dict]:
    """Retrieve relevant chunks from persistent website and sermon documents."""
    documents = db.research_sources(church_id)
    if not documents:
        urls = []
        for kind in ("medium", "deep"):
            j = db.latest_job(church_id, kind)
            if _retained_job(j, kind) and j.get("result_json"):
                try:
                    urls.extend((p["url"], 90 if kind == "medium" else 365) for p in json.loads(j["result_json"]).get("pages", []))
                except (ValueError, KeyError, TypeError):
                    pass
        for url, keep_days in dict.fromkeys(urls):
            hit = db.cache_get(url) or db.cache_get(url.rstrip("/")) or db.cache_get(url.rstrip("/") + "/")
            if hit and _retained(hit[0], keep_days):
                try:
                    documents.append({"url": url, "text": json.loads(hit[1]).get("text", "")})
                except ValueError:
                    pass
    terms, chunks = _terms(question), []
    for d in documents:
        text = d.get("text", "")
        for offset in range(0, len(text), 2200):
            chunk = text[offset:offset + 2800]
            relevance = len(terms & _terms(chunk)) + len(terms & _terms(d.get("title", "") + " " + d.get("kind", "")))
            chunks.append((relevance, offset, {"url": d["url"], "text": chunk, "kind": d.get("kind", "website"),
                                              "checked_at": d.get("checked_at"), "title": d.get("title", ""),
                                              "speaker": d.get("speaker", ""), "published_at": d.get("published_at", "")}))
    chunks.sort(key=lambda x: (-x[0], x[1]))
    selected, used = [], 0
    for _, _, chunk in chunks:
        chunk["text"] = chunk["text"][:max_chars - used]
        if not chunk["text"]:
            break
        selected.append(chunk)
        used += len(chunk["text"])
    return selected


def _lookup(church_id: str, question: str, church) -> list[dict]:
    """At most three public resource reads; never creates a deep-dive job."""
    from app.web import Blocked, fetch
    candidates = []
    for kind in ("medium", "deep"):
        job = db.latest_job(church_id, kind)
        if _retained_job(job, kind) and job.get("result_json"):
            try:
                candidates += json.loads(job["result_json"]).get("resources", [])
            except (ValueError, TypeError):
                pass
    candidates += [{"url": s["url"], "kind": s.get("kind", ""), "label": s.get("title", "")} for s in db.research_sources(church_id)]
    if church and church.website:
        candidates.append({"url": church.website, "kind": "home", "label": "services staff ministries"})
    terms = _terms(question)
    # Common question vocabulary maps to resource descriptions, not guessed facts.
    if terms & {"upcoming", "event", "events", "schedule", "calendar"}:
        terms |= {"calendar", "events"}
    if terms & {"groups", "group", "small"}:
        terms |= {"groups", "ministries"}
    candidates.sort(key=lambda r: -len(terms & _terms(r.get("kind", "") + " " + r.get("label", "") + " " + r.get("url", ""))))
    out, seen = [], set()
    for r in candidates:
        if len(seen) >= 3:
            break
        url = r.get("url", "")
        if url in seen or not url.startswith(("http://", "https://")):
            continue
        seen.add(url)
        try:
            page = fetch(url, max_age_days=0 if r.get("kind") == "calendar" else 7)
        except Blocked:
            continue
        if page.get("status") == 200 and page.get("text"):
            # Calendar text is transient, never added to the deep corpus.
            out.append({"url": page["url"], "text": page["text"][:12000]})
            if r.get("kind") not in {"calendar", "events", "registration"} and not page.get("from_cache"):
                db.research_put(church_id, page["url"], page["text"], kind=r.get("kind", "website"), scope="medium")
        if len(seen) >= 3:
            break
    return out


def answer(session_id: str, church_id: str, question: str, *, record: bool = True) -> dict:
    """{"answer": str, "sources": [{url, quote}], "confident": bool}. Not confident -> added to Open questions."""
    from app.stage1 import search

    from app import memory
    about_you = memory.context(session_id)
    about_you["history"] = about_you.get("history", [])[-20:]
    ch = search.get_church(session_id, church_id)
    name = ch.name if ch else "that church"
    evidence = db.get_evidence(church_id) + (list(ch.denomination.evidence) if ch else [])
    evidence = [e for e in evidence if _retained(e.checked_at, 365 if e.source_kind in ("sermon_transcript", "sermon_feed", "livestream") else 90)]
    facts = [{"feature": e.feature, "value": e.value, "quote": e.quote, "url": e.url} for e in evidence
             if e.value not in ("unknown", "unstated")][:40]
    pages = _pages(church_id, question=question)
    # Changing collections are refreshed for time-sensitive questions even if old text has an answer.
    looked_up = bool(_terms(question) & {"upcoming", "calendar", "events", "tonight", "tomorrow"})
    if looked_up:
        pages = _lookup(church_id, question, ch)
    result = {"answer": "", "sources": [], "confident": False}
    for attempt in range(2):
        if attempt == 1 and not looked_up:
            pages = (_lookup(church_id, question, ch) + pages)[:10]
        if attempt == 1 and looked_up:
            break
        if not (facts or pages):
            continue
        try:
            out = llm.complete_json("church_qa", [
                {"role": "system", "content": QA_SYSTEM},
                {"role": "user", "content": json.dumps({"church": name, "question": question, "facts": facts, "pages": pages, "about_you": about_you})}],
                QA_SCHEMA, tier="fast")
            corpus = {}
            for p in pages:
                corpus.setdefault(p["url"], []).append(p["text"])
            for e in evidence:
                corpus.setdefault(e.url, []).append(e.quote or "")
            good = [s for s in out.get("sources", []) if isinstance(s, dict) and isinstance(s.get("quote"), str) and len(s["quote"].strip()) >= 10
                    and str(s.get("url", "")).startswith(("https://", "http://"))
                    and any(re.sub(r"\s+", " ", s["quote"].strip().lower()) in re.sub(r"\s+", " ", c.lower()) for c in corpus.get(s.get("url"), []) if c)]
            result = {"answer": out.get("answer", ""), "sources": good[:2], "confident": bool(out.get("confident")) and bool(good)}
        except Exception:
            pass
        if result["confident"]:
            break
    if not result["confident"]:
        if record:
            have = {q["text"].lower() for q in open_questions(session_id, church_id)}
            if question.lower() not in have:
                add_question(session_id, church_id, question.strip())
        lead = ""  # Unverified model prose is not presented as a church fact.
        result["answer"] = (lead + f"I haven't seen a clear answer to that on {name}'s website yet. " +
                            ("I've added it to your Open questions — we can investigate further, or you could ask them directly." if record else "You could ask them directly on a visit."))
    elif result["sources"]:
        result["answer"] += f"\n\n(From the source: \"{result['sources'][0]['quote'][:200]}\")"
    return result


def open_questions(session_id: str, church_id: str | None = None) -> list[dict]:
    from app.stage1 import search
    rows = db.questions_list(session_id, church_id)
    active = []
    for row in rows:
        church = search.get_church(session_id, row["church_id"])
        if church is None:
            continue
        row["church_name"] = church.name
        active.append(row)
    return active


def add_question(session_id: str, church_id: str, text: str) -> int:
    return db.question_add(session_id, church_id, text)


def update_question(qid: int, *, text: str | None = None, status: str | None = None) -> None:
    fields = {k: v for k, v in (("text", text), ("status", status)) if v is not None}
    db.question_update(qid, **fields)
