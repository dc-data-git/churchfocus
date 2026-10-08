"""Stage 2 card — website extraction + deep-dive rating (INTERFACES §4)."""
from __future__ import annotations

import re
import json
from datetime import datetime, timezone

from rapidfuzz import fuzz

from app import db
from app.llm import complete_json, load_prompt
from app.features import all_features, feature, settled_open
from app.match import score
from app.models import Church, Evidence, PreferenceProfile
from app.evidence_rules import marriage_rule_violation, value_ok
from app.stage2.website import site_pages

_VERBATIM_THRESHOLD = 90
_MARRIAGE_RE = re.compile(
    r"one man.*one woman|man and woman|between a man and a woman|husband and wife",
    re.I,
)

_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["features"],
    "properties": {
        "facts": {"type": "array", "items": {"type": "object"}},
        "staff": {"type": "array", "items": {"type": "object"}},
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["feature", "status"],
                "properties": {
                    "feature": {"type": "string"},
                    "status": {"type": "string"},
                    "value": {"type": "string"},
                    "quote": {"type": "string"},
                    "url": {"type": "string"},
                },
            },
        }
    },
}

_SOURCE_KIND = {
    "beliefs": "statement_of_faith",
    "staff": "staff_page",
    "home": "website",
    "about": "website",
    "ministries": "website",
    "events": "website",
    "sermons": "sermon_feed",
    "other": "website",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def quote_verbatim(quote: str, page_text: str, threshold: int = _VERBATIM_THRESHOLD) -> bool:
    q, t = _norm(quote), _norm(page_text)
    if len(q) < 12:
        return False
    if q in t:
        return True
    return fuzz.partial_ratio(q, t) >= threshold


def _stage2_feature_ids() -> list[str]:
    return [fid for fid, meta in all_features().items() if meta.get("stage", 99) <= 2]


def _source_for_kind(kind: str) -> str:
    return _SOURCE_KIND.get(kind, "website")


def _apply_marriage_rule(items: list[dict]) -> list[dict]:
    """Marriage statements settle lgbtq.marriage only — never lgbtq.inclusion (shared rule, R10).
    Affirming statements are kept (the old filter dropped anything that wasn't 'man and woman')."""
    out: list[dict] = []
    for item in items:
        if item.get("status") == "stated" and marriage_rule_violation(
                item.get("feature", ""), item.get("value", ""), item.get("quote", "")):
            continue
        out.append(item)
    return out


def _extract_page(page: dict, feature_ids: list[str], cancelled=None) -> list[dict]:
    if not page.get("text") or not feature_ids:
        return []
    prompt = load_prompt("medium_extract.v1")
    # The model must know the allowed values, or nearly every value it writes is rejected below.
    lines = [f"- {fid}: {feature(fid)['label']}; allowed values: {feature(fid)['values']}" for fid in feature_ids]
    source = page.get("scan_text", page["text"])
    features, facts, staff = [], [], []
    for start in range(0, len(source), 15000):
        if cancelled and cancelled():
            break
        user = (f"Target church: {page.get('church_name', 'this church')}\nPage URL: {page['url']}\nPage kind: {page['kind']}\n\n"
                + "Requested features (use ONLY the allowed values):\n" + "\n".join(lines)
                + f"\n\nPage text:\n{source[start:start + 17000]}")
        result = complete_json("page_extract", [{"role": "system", "content": prompt},
                              {"role": "user", "content": user}], _EXTRACT_SCHEMA, tier="fast")
        features.extend(result.get("features", []))
        facts.extend(result.get("facts", []))
        staff.extend(result.get("staff", []))
    page["extracted_facts"] = facts
    page["extracted_staff"] = staff
    return features


def _to_evidence(item: dict, page: dict, page_text: str) -> Evidence | None:
    if item.get("status") != "stated":
        return None
    quote = (item.get("quote") or "").strip()
    if not quote or not quote_verbatim(quote, page_text):
        return None
    fid = item.get("feature", "")
    if fid not in all_features():
        return None
    value = item.get("value", "")
    if not value_ok(fid, value) or marriage_rule_violation(fid, value, quote):
        return None
    return Evidence(
        feature=fid,
        value=value,
        tier="A",
        quote=quote[:300],
        url=page["url"],  # The verified quote belongs to this fetched page.
        source_kind=_source_for_kind(page["kind"]),
        how="stated",
        checked_at=datetime.now(timezone.utc),
    )


def _deep_dive_rating(pages: list[dict], open_features: list[str], profile: PreferenceProfile) -> tuple[str, str]:
    kinds = {p["kind"] for p in pages}
    has_sermons = "sermons" in kinds or any("sermon" in p["url"].lower() for p in pages)
    has_beliefs = "beliefs" in kinds or any("belief" in p["url"].lower() for p in pages)
    important_open = [
        f
        for f in open_features
        if any(p.weight == "important" and p.feature == f for p in profile.preferences)
    ]
    if has_sermons:
        return "strong", "Sermon/media archive available for researching teaching and practice"
    if not has_sermons and not has_beliefs:
        return "weak", "No beliefs or sermon pages found on the website"
    if has_sermons:
        return "possible", "Sermon/media page found; most important features settled"
    return "possible", "Beliefs page found; sermon analysis could fill remaining gaps"


IDENTITY_FEATURES = ["identity.denomination", "identity.tradition", "logistics.service_times", "logistics.language",
                     "polity.leaders", "community.ministries", "worship.style", "community.kids", "community.youth",
                     "community.small_groups"]
_PAGE_RANK = {"beliefs": 0, "staff": 1, "about": 2, "home": 3, "ministries": 4, "events": 5, "sermons": 6, "other": 7}


def _better(a: Evidence, b: Evidence, kind_a: str, kind_b: str) -> bool:
    """U20: keep one evidence per feature — prefer the beliefs/staff/about page, then a fuller sentence quote."""
    ra, rb = _PAGE_RANK.get(kind_a, 9), _PAGE_RANK.get(kind_b, 9)
    if ra != rb:
        return ra < rb
    def quality(q: str) -> int:
        q = q.strip()
        return len(q) + (40 if q[:1].isupper() and q.rstrip().endswith((".", "!", "?")) else 0) - (60 if q.isupper() else 0)
    return quality(a.quote) > quality(b.quote)


def medium_search(church: Church, profile: PreferenceProfile, *, progress=None, cancelled=None, max_pages: int = 30) -> dict:
    """Run Stage 2 on one church; return card dict with deep_dive_candidate.
    Broad baseline facts and staff, recursive public sources, incremental refresh, cooperative cancellation."""
    from concurrent.futures import ThreadPoolExecutor

    from app.denom.kb import get_kb

    kb = get_kb()
    progress = progress or (lambda *a: None)
    cancelled = cancelled or (lambda: False)
    if not church.website:
        match = score(church, profile, kb)
        feature_ids = [p.feature for p in profile.preferences]
        settled, open_f = settled_open(church.evidence, feature_ids)
        return {"church": church, "match": match, "settled": settled, "open": open_f, "evidence": [], "pages": [],
                "deep_dive_candidate": "weak", "reason": "No website on file", "facts": [], "staff": [], "resources": [],
                "coverage": {"pages_scanned": 0, "limited": False, "failures": [], "missing_basics": ["service times", "public staff", "ministries"]}}

    prior_sources = {r["url"]: r for r in db.research_sources(church.church_id, scope="medium")}
    prior_result = {}
    prior_job = db.latest_job(church.church_id, "medium")
    if prior_job and prior_job.get("result_json"):
        try:
            prior_result = json.loads(prior_job["result_json"])
        except (ValueError, TypeError):
            pass
    progress(0, max_pages + 1, f"Opening {church.name}'s website…")
    coverage, resources = {}, []
    pages = site_pages(church.website, max_pages=max_pages, cancelled=cancelled, progress=progress,
                       coverage=coverage, resources=resources)
    if cancelled():
        return {"church": church, "cancelled": True}
    if not pages:
        raise RuntimeError("No permitted readable website pages; research could not complete")
    coverage["extractor_version"] = "medium_extract.v1/published-embedded"
    total = len(pages)
    wanted = [p.feature for p in profile.preferences if p.weight != "dont_care"]
    stage2_ids = [f for f in all_features() if all_features()[f].get("stage", 9) <= 3]
    done = {"n": 0}
    lock = __import__("threading").Lock()

    def work(page: dict) -> list[tuple[Evidence, str]]:
        page["church_name"] = church.name
        if cancelled():
            return []
        unchanged = (page["url"] in prior_sources and prior_sources[page["url"]]["text"] == page["text"]
                     and "facts" in prior_result and "staff" in prior_result
                     and prior_result.get("coverage", {}).get("extractor_version") == coverage["extractor_version"])
        if unchanged:
            page["extracted_facts"] = [f for f in prior_result["facts"] if f.get("url") == page["url"]]
            page["extracted_staff"] = [f for f in prior_result["staff"] if f.get("url") == page["url"]]
            with lock:
                done["n"] += 1
                progress(done["n"], total, f"Verified {church.name}'s website — {done['n']} of {total} pages")
            return [(e, page["kind"]) for e in db.get_evidence(church.church_id) if e.url == page["url"]]
        try:
            raw = _apply_marriage_rule(_extract_page(page, stage2_ids, cancelled=cancelled))
        except Exception:   # expose extraction gaps instead of silently claiming success
            with lock:
                coverage["failures"].append({"url": page["url"], "reason": "factual extraction failed"})
            raw = []
        out = []
        for item in raw:
            ev = _to_evidence(item, page, page["text"])
            if ev is not None:
                out.append((ev, page["kind"]))
        with lock:
            done["n"] += 1
            progress(done["n"], total, f"Reading {church.name}'s website — {done['n']} of {total} pages")
        return out

    best: dict[str, tuple[Evidence, str]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for items in pool.map(work, pages):
            for ev, kind in items:
                cur = best.get(ev.feature)
                if cur is None or _better(ev, cur[0], kind, cur[1]):
                    best[ev.feature] = (ev, kind)
    if cancelled():
        return {"church": church, "cancelled": True}
    new_evidence = [ev for ev, _ in best.values()]

    if new_evidence:
        db.add_evidence(church.church_id, new_evidence)
        db.upsert_church(church.church_id, church.website)

    facts, staff = [], []
    now = datetime.now(timezone.utc).isoformat()
    for page in pages:
        if cancelled():
            return {"church": church, "cancelled": True}
        # Do not move a source timestamp forward merely by copying cached text.
        previous = {r["url"] for r in db.research_sources(church.church_id, scope="medium")}
        if not page.get("from_cache") or page["url"] not in previous:
            db.research_put(church.church_id, page["url"], page["text"], kind=page["kind"], scope="medium", checked_at=page.get("checked_at"))
        for item in page.get("extracted_facts", []):
            if not isinstance(item, dict) or not item.get("label") or not item.get("value"):
                continue
            quote = item.get("quote", "")
            if not isinstance(quote, str) or not _norm(quote) or _norm(quote) not in _norm(page["text"]):
                continue
            if any((_norm(f["label"]), _norm(f["value"])) == (_norm(str(item["label"])), _norm(str(item["value"]))) for f in facts):
                continue
            facts.append({"label": str(item["label"]), "value": str(item["value"]), "quote": quote,
                          "url": page["url"], "kind": page["kind"], "tier": "A", "checked_at": page.get("checked_at") or now})
        for item in page.get("extracted_staff", []):
            if not isinstance(item, dict) or not item.get("name") or not item.get("position"):
                continue
            quote = item.get("quote", "")
            if not isinstance(quote, str) or not _norm(quote) or _norm(quote) not in _norm(page["text"]):
                continue
            if _norm(item["name"]) not in _norm(quote) or _norm(item["position"]) not in _norm(quote):
                continue
            row = {"name": str(item["name"]), "position": str(item["position"]), "quote": quote, "url": page["url"],
                   "tier": "A", "checked_at": page.get("checked_at") or now}
            if not any((_norm(x["name"]), _norm(x["position"])) == (_norm(row["name"]), _norm(row["position"])) for x in staff):
                staff.append(row)
    fact_labels = " ".join(f["label"].lower() for f in facts)
    if coverage.get("failures") and not pages:
        coverage["limited"] = True
        coverage["limit_reason"] = "Could not read substantive website content"
    coverage["missing_basics"] = (["service times"] if not any(e.feature == "logistics.service_times" for e in new_evidence)
          and not re.search("service|worship.*time", fact_labels) else [])
    if not staff:
        coverage["missing_basics"].append("public staff names and positions")
    if not any(e.feature in ("community.ministries", "community.small_groups") for e in new_evidence) and "ministr" not in fact_labels:
        coverage["missing_basics"].append("active ministries")
    all_evidence = church.evidence + new_evidence
    updated = church.model_copy(update={"evidence": all_evidence, "stage_done": max(church.stage_done, 2)})
    match = score(updated, profile, kb)
    feature_ids = [p.feature for p in profile.preferences]
    settled, open_f = settled_open(all_evidence, feature_ids)
    rating, reason = _deep_dive_rating(pages, open_f, profile)
    if coverage.get("failures") and not pages:
        rating, reason = "unknown", "Could not read the website; deep-dive potential has not been assessed"
        coverage["missing_basics"] = []
    return {"church": updated, "match": match, "settled": settled, "open": open_f, "evidence": new_evidence,
            "pages": [{"url": p["url"], "kind": p["kind"], "checked_at": p.get("checked_at")} for p in pages],
            "facts": facts, "staff": staff, "resources": resources, "coverage": coverage,
            "deep_dive_candidate": rating, "reason": reason}
