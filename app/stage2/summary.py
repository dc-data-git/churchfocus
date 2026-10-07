"""Stage 2 card — website extraction + deep-dive rating (INTERFACES §4)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from rapidfuzz import fuzz

from app import db
from app.llm import complete_json, load_prompt
from app.features import all_features, feature, settled_open
from app.match import score
from app.models import Church, Evidence, PreferenceProfile
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
    """Marriage statements settle lgbtq.marriage only — never lgbtq.inclusion."""
    out: list[dict] = []
    for item in items:
        fid = item.get("feature", "")
        if fid == "lgbtq.inclusion":
            continue
        if fid == "lgbtq.marriage" and item.get("status") == "stated":
            quote = item.get("quote", "")
            if quote and not _MARRIAGE_RE.search(quote):
                continue
        out.append(item)
    return out


def _extract_page(page: dict, feature_ids: list[str]) -> list[dict]:
    if not page.get("text") or not feature_ids:
        return []
    prompt = load_prompt("page_extract.v1")
    labels = {fid: feature(fid)["label"] for fid in feature_ids}
    user = (
        f"Page URL: {page['url']}\n"
        f"Page kind: {page['kind']}\n\n"
        f"Requested features:\n"
        + "\n".join(f"- {fid}: {labels[fid]}" for fid in feature_ids)
        + f"\n\nPage text:\n{page['text'][:15000]}"
    )
    result = complete_json(
        "page_extract",
        [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
        _EXTRACT_SCHEMA,
        tier="fast",
    )
    return result.get("features", [])


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
    allowed = feature(fid)["values"]
    if value not in allowed:
        return None
    return Evidence(
        feature=fid,
        value=value,
        tier="A",
        quote=quote[:300],
        url=item.get("url") or page["url"],
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
    if has_sermons and important_open:
        return "strong", f"Sermon/media page found; {len(important_open)} important feature(s) still open"
    if not has_sermons and not has_beliefs:
        return "weak", "No beliefs or sermon pages found on the website"
    if has_sermons:
        return "possible", "Sermon/media page found; most important features settled"
    return "possible", "Beliefs page found; sermon analysis could fill remaining gaps"


def medium_search(church: Church, profile: PreferenceProfile) -> dict:
    """Run Stage 2 on one church; return card dict with deep_dive_candidate."""
    if not church.website:
        match = score(church, profile)
        feature_ids = [p.feature for p in profile.preferences]
        settled, open_f = settled_open(church.evidence, feature_ids)
        rating, reason = "weak", "No website on file"
        return {
            "church": church,
            "match": match,
            "settled": settled,
            "open": open_f,
            "evidence": [],
            "pages": [],
            "deep_dive_candidate": rating,
            "reason": reason,
        }

    pages = site_pages(church.website)
    stage2_ids = _stage2_feature_ids()
    new_evidence: list[Evidence] = []

    for page in pages:
        raw = _extract_page(page, stage2_ids)
        raw = _apply_marriage_rule(raw)
        for item in raw:
            ev = _to_evidence(item, page, page["text"])
            if ev is not None:
                new_evidence.append(ev)

    if new_evidence:
        db.add_evidence(church.church_id, new_evidence)
        db.upsert_church(church.church_id, church.website)

    all_evidence = church.evidence + new_evidence
    updated = church.model_copy(update={"evidence": all_evidence, "stage_done": max(church.stage_done, 2)})
    match = score(updated, profile)
    feature_ids = [p.feature for p in profile.preferences]
    settled, open_f = settled_open(all_evidence, feature_ids)
    rating, reason = _deep_dive_rating(pages, open_f, profile)

    return {
        "church": updated,
        "match": match,
        "settled": settled,
        "open": open_f,
        "evidence": new_evidence,
        "pages": [{"url": p["url"], "kind": p["kind"]} for p in pages],
        "deep_dive_candidate": rating,
        "reason": reason,
    }
