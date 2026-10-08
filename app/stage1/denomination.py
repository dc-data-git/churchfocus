"""Stage 1 denomination resolution and light search (ARCHITECTURE §5–6)."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.denom.kb import get_kb
from app.match import score
from app.models import Church, DenomGuess, Evidence, MatchResult, PreferenceProfile

from . import osm, places

NON_DENOM = re.compile(r"\b(non[- ]?denominational|nondenominational|independent)\b", re.I)   # church NAMES
NON_DENOM_TEXT = re.compile(r"\b(non[- ]?denominational|nondenominational)\b", re.I)          # page text ("independent" is too common)

# R9: the locator cross-search costs ~7 paid web searches per church for little gain until search results
# carry reliable URLs. Off for the demo (BUILD_PLAN cut-order #3); flip to True to re-enable.
LOCATOR_ENABLED = False

# domain -> KB id (contracts/source_registry.md)
LOCATORS: list[tuple[str, str]] = [
    ("sbc.net", "sbc"),
    ("mennoniteusa.org", "mcusa"),
    ("elca.org", "elca"),
    ("ag.org", "ag"),
    ("umc.org", "umc"),
    ("pcusa.org", "pcusa"),
    ("nazarene.org", "nazarene"),
]


def score_to_confidence(score: float) -> float:
    """INTERFACES: 100→0.9, ≥88→0.8, ≥85→0.6, 70→0.3."""
    if score >= 100:
        return 0.9
    if score >= 88:
        return 0.8
    if score >= 85:
        return 0.6
    if score >= 70:
        return 0.3
    return 0.2


def _city_from_address(address: str) -> str:
    parts = [p.strip() for p in address.split(",")]
    return parts[-2] if len(parts) >= 2 else ""


def _name_in_text(name: str, text: str) -> bool:
    core = re.sub(r"\b(church|chapel|fellowship|community)\b", "", name, flags=re.I).strip()
    return bool(core) and core.lower() in text.lower()


def _locator_search(name: str, city: str, kb) -> DenomGuess | None:
    from app import llm

    for domain, did in LOCATORS:
        if did not in kb.groups:
            continue
        query = f'site:{domain} "{name}" {city}'.strip()
        for hit in llm.web_search(query, max_results=3):
            blob = f"{hit.get('url', '')} {hit.get('title', '')} {hit.get('snippet', '')}"
            if domain in blob.lower() and _name_in_text(name, blob):
                return DenomGuess(
                    denomination_id=did,
                    label=kb.groups[did]["name"],
                    confidence=0.95,
                    method="locator",
                    evidence=[
                        Evidence(
                            feature="identity.denomination",
                            value=did,
                            tier="B",
                            quote=hit.get("snippet", "")[:200],
                            url=hit.get("url", ""),
                            source_kind="denomination_locator",
                            how="observed",
                        )
                    ],
                )
    return None


DENOM_CLASSIFY_SCHEMA = {
    "type": "object",
    "required": ["label", "kb_candidates", "independent", "confidence", "quote"],
    "properties": {
        "label": {"type": "string"},
        "kb_candidates": {"type": "array", "items": {"type": "string"}},
        "independent": {"type": "boolean"},
        "confidence": {"type": "number"},
        "quote": {"type": "string"},
        "url": {"type": "string"},
    },
}


def _kb_menu(kb, k: int = 60) -> str:
    """Largest Christian groups as 'id: name' lines, so the model can pick KB ids (denom_classify.v1)."""
    ids = sorted((g for g in kb.groups if kb.is_christian(g)),
                 key=lambda g: -(kb.groups[g]["census_2020"].get("adherents") or 0))[:k]
    return "\n".join(f"{g}: {kb.groups[g]['name']}" for g in ids)


def _verbatim(quote: str, text: str) -> bool:
    from rapidfuzz import fuzz
    return bool(quote) and fuzz.partial_ratio(quote.lower(), text.lower()) >= 90


def _website_classify(name: str, website: str, kb) -> DenomGuess:
    from app import llm, web

    try:
        page = web.fetch(website, max_chars=12000)
    except Exception:
        return DenomGuess(label="Unknown (likely independent)", confidence=0.2, method="unknown")

    text = page.get("text") or ""
    if not text.strip():
        return DenomGuess(label="Unknown (website unreadable)", confidence=0.2, method="unknown")
    if NON_DENOM_TEXT.search(text):
        m = NON_DENOM_TEXT.search(text)
        snippet = text[max(0, m.start() - 80): m.end() + 80].strip()
        return DenomGuess(denomination_id=None, label="Non-denominational", confidence=0.85, method="website",
                          evidence=[Evidence(feature="identity.denomination", value="nondenominational", tier="A",
                                             quote=snippet, url=website, source_kind="website", how="stated")])

    try:
        out = llm.complete_json(
            "denom_classify",
            [{"role": "system", "content": llm.load_prompt("denom_classify.v1")},
             {"role": "user", "content": f"Church: {name}\nURL: {website}\n\nKB list (id: name):\n{_kb_menu(kb)}"
                                         f"\n\nExcerpts from the church's website:\n{text[:8000]}"}],
            DENOM_CLASSIFY_SCHEMA,
            tier="fast",
        )
    except Exception:
        return DenomGuess(label="Unknown (likely independent)", confidence=0.2, method="unknown")

    quote = (out.get("quote") or "").strip()
    conf = float(out.get("confidence") or 0.0)
    label = out.get("label") or "Unknown"
    cands = [c for c in (out.get("kb_candidates") or []) if c in kb.groups]
    did = cands[0] if cands else None
    evidence: list[Evidence] = []
    if quote and _verbatim(quote, text):
        evidence.append(Evidence(feature="identity.denomination", value=did or label, tier="A", quote=quote[:240],
                                 url=website, source_kind="website", how="stated"))
    else:
        conf = min(conf, 0.5)   # no verifiable quote -> never "confident"
    if out.get("independent") and not did:
        return DenomGuess(denomination_id=None, label="Non-denominational", confidence=max(min(conf, 0.85), 0.6),
                          method="website", evidence=evidence)

    if did and did in kb.groups:
        return DenomGuess(denomination_id=did, label=kb.groups[did]["name"], confidence=min(conf, 0.9), method="website", evidence=evidence)

    hits = kb.find(label) or kb.find(name)
    if hits:
        top = hits[0]
        return DenomGuess(
            denomination_id=top["id"],
            label=top["name"],
            confidence=max(score_to_confidence(top["score"]), min(conf, 0.9)) if evidence else min(conf, 0.5),
            method="website",
            evidence=evidence,
        )
    if NON_DENOM.search(label):
        return DenomGuess(denomination_id=None, label="Non-denominational", confidence=max(conf, 0.8), method="website", evidence=evidence)
    return DenomGuess(denomination_id=None, label=label, confidence=min(conf, 0.7), method="website", evidence=evidence)


def resolve(candidate: dict, *, use_website: bool = True) -> DenomGuess:
    """Resolve denomination for a Places/OSM candidate (ARCHITECTURE §5 steps 1–5)."""
    kb = get_kb()
    name = candidate.get("name") or ""
    website = candidate.get("website")
    address = candidate.get("address") or ""
    osm_denom = candidate.get("osm_denomination")

    if NON_DENOM.search(name):
        return DenomGuess(denomination_id=None, label="Non-denominational", confidence=0.8, method="name")

    hits = kb.find(name)
    if hits:
        top = hits[0]
        conf = score_to_confidence(top["score"])
        best = DenomGuess(denomination_id=top["id"], label=top["name"], confidence=conf, method="name")
        if conf >= 0.8:
            return best
    else:
        best = DenomGuess(label="Unknown (likely independent)", confidence=0.2, method="unknown")

    if osm_denom and best.confidence < 0.8:
        osm_hits = kb.find(osm_denom)
        if osm_hits:
            guess = DenomGuess(
                denomination_id=osm_hits[0]["id"],
                label=osm_hits[0]["name"],
                confidence=0.6,
                method="name",
                evidence=[
                    Evidence(
                        feature="identity.denomination",
                        value=osm_hits[0]["id"],
                        tier="C",
                        quote=osm_denom,
                        source_kind="osm",
                        how="observed",
                    )
                ],
            )
            if guess.confidence > best.confidence:
                best = guess

    if LOCATOR_ENABLED and best.confidence < 0.8:
        locator = _locator_search(name, _city_from_address(address), kb)
        if locator:
            if locator.confidence >= 0.8:
                return locator
            if locator.confidence > best.confidence:
                best = locator

    if use_website and website and best.confidence < 0.8:
        site_guess = _website_classify(name, website, kb)
        if site_guess.confidence >= 0.8:
            return site_guess
        if site_guess.confidence > best.confidence:
            best = site_guess

    if use_website and best.confidence < 0.8 and website:
        try:
            page = __import__("app.web", fromlist=["fetch"]).fetch(website, max_chars=8000)
            if NON_DENOM_TEXT.search(page.get("text") or ""):
                return DenomGuess(denomination_id=None, label="Non-denominational", confidence=0.8, method="website")
        except Exception:
            pass

    if not best.denomination_id and best.confidence <= 0.3:
        return DenomGuess(label="Unknown (likely independent)", confidence=0.4, method="unknown")
    return best


def _candidate_to_church(c: dict, denom: DenomGuess) -> Church:
    return Church(
        church_id=c["church_id"],
        name=c["name"],
        address=c.get("address") or "",
        lat=c.get("lat") or 0.0,
        lng=c.get("lng") or 0.0,
        website=c.get("website"),
        phone=c.get("phone"),
        distance_miles=c.get("distance_miles") or 0.0,
        denomination=denom,
        evidence=list(denom.evidence),
        stage_done=1,
    )


def light_search(profile: PreferenceProfile) -> list[tuple[Church, MatchResult]]:
    """Geocode → Places (OSM fallback) → resolve top 20 → match.score → sort."""
    kb = get_kb()
    origin = profile.origin or {}
    text = origin.get("text") or ""
    if "lat" in origin and "lng" in origin:
        lat, lng = float(origin["lat"]), float(origin["lng"])
    elif text:
        lat, lng = places.geocode(text)
    else:
        raise ValueError("profile.origin needs text or lat/lng")

    radius_m = int(profile.max_miles * 1609.34)
    try:
        candidates = places.search_churches(lat, lng, radius_m)
        if profile.likely_denominations:
            trad = kb.groups.get(profile.likely_denominations[0], {}).get("name") or profile.likely_denominations[0]
            extra = places.search_churches(lat, lng, radius_m, query=f"{trad} church")
            by_id = {c["church_id"]: c for c in candidates}
            for c in extra:
                by_id.setdefault(c["church_id"], c)
            candidates = sorted(by_id.values(), key=lambda x: x["distance_miles"])
    except Exception:
        candidates = osm.search_churches_osm(lat, lng, radius_m)

    candidates = [c for c in candidates if c.get("distance_miles", 999) <= profile.max_miles]
    top = candidates[:20]

    resolved: list[tuple[dict, DenomGuess]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(resolve, c): c for c in top}
        for fut in as_completed(futs):
            try:
                guess = fut.result()
            except Exception:   # one bad website must not fail the whole search
                guess = DenomGuess(label="Unknown", confidence=0.2, method="unknown")
            resolved.append((futs[fut], guess))

    results: list[tuple[Church, MatchResult]] = []
    for cand, denom in resolved:
        church = _candidate_to_church(cand, denom)
        mr = score(church, profile, kb)
        if not mr.excluded:
            results.append((church, mr))

    results.sort(key=lambda x: (-x[1].score, x[0].distance_miles))
    return results
