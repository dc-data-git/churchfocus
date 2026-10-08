"""Deterministic, explainable matcher (ARCHITECTURE §6, D20). The ONLY place that scores."""
from __future__ import annotations

from typing import Callable

from .features import data_points, feature
from .models import Church, Evidence, MatchResult, PreferenceProfile

WEIGHT = {"dealbreaker": 5.0, "important": 3.0, "nice_to_have": 1.0, "dont_care": 0.0}
TIER_RANK = {"A": 5, "B": 4, "D": 3, "C": 2, "prior": 1}
PLACEHOLDER = {"miles", "schedule", "language_list", "ministry_list", "topic_list", "network_name", "names_roles_tenure",
               "change_list", "report_list", "stated_position_text"}
UNKNOWN_VALUES = {"unknown", "unstated", "not_found", ""}
KIND_PHRASE = {"website": "website", "statement_of_faith": "beliefs page", "staff_page": "staff page", "calendar": "calendar",
               "sermon_feed": "sermons", "sermon_transcript": "sermons", "livestream": "livestream", "wayback": "archived website",
               "denomination_locator": "denomination directory", "denomination_stats": "denomination statistics",
               "news": "local news", "places_api": "Google Maps", "osm": "OpenStreetMap"}


def strength(e: Evidence, denom_conf: float, var: float) -> float:
    if e.tier == "A":
        return 1.0
    if e.tier == "B":
        return 0.8
    if e.tier == "C":
        return 0.5
    if e.tier == "D":
        return 0.6 if data_points(e.note) >= 5 else 0.4
    if e.feature.startswith("identity."):        # prior on identity: as certain as the denomination itself
        return denom_conf
    return 0.5 * denom_conf * (1 - var)


def agreement(e: Evidence | None, want: list[str], fid: str, avoid: list[str] | None = None) -> int:
    """+1 matches, -1 conflicts (incl. an avoided value), 0 unknown."""
    avoid = avoid or []
    if e is None or e.value.strip().lower() in UNKNOWN_VALUES:
        return 0
    vals = [v.strip() for v in e.value.split(";") if v.strip()]
    if set(feature(fid)["values"]) & PLACEHOLDER:  # free-form values: loose text match
        low = e.value.lower()
        if any(a.lower() in low for a in avoid):
            return -1
        if want:
            return 1 if any(w.lower() in low for w in want) else -1
        return 0
    if any(v in avoid for v in vals):
        return -1
    if want:
        return 1 if any(v in want for v in vals) else -1
    return 1 if avoid else 0   # only avoids given, and this value isn't one of them


def best_evidence(evidence: list[Evidence], fid: str) -> Evidence | None:
    ev = [e for e in evidence if e.feature == fid]
    return max(ev, key=lambda e: (TIER_RANK[e.tier], e.checked_at)) if ev else None


def _phrase(e: Evidence, denom_label: str) -> str:
    if e.tier == "A":
        return f"their {KIND_PHRASE.get(e.source_kind, e.source_kind.replace('_', ' '))} says so"
    if e.tier == "B":
        return f"{KIND_PHRASE.get(e.source_kind, e.source_kind.replace('_', ' '))} reports it"
    if e.tier == "C":
        return "a third-party listing suggests it"
    if e.tier == "D":
        return f"inferred: {e.note}" if e.note else "inferred"
    if e.feature.startswith("identity."):
        return f"from its denomination ({denom_label})"
    return f"typical for {denom_label}; may differ locally"


IDENTITY_FILTER = {"identity.denomination", "identity.tradition", "identity.branch"}


def fit_label(score: float, known_share: float) -> str:
    """D31: Strong · Possible · Not enough info yet · Unlikely · Poor."""
    if known_share < 0.4:
        return "unknown"
    if score >= 70:
        return "strong"
    if score >= 58:
        return "possible"
    if score >= 45:
        return "unlikely"
    return "poor"


def score_evidence(church_id: str, profile: PreferenceProfile, best: dict[str, Evidence], denom_conf: float = 0.0,
                   variability: Callable[[str], float] = lambda f: 0.3, denom_label: str = "its denomination",
                   distance_miles: float | None = None) -> MatchResult:
    """v2 (D28): nothing is excluded for practice/belief preferences — they only move fit. A church is excluded only
    when its denomination/tradition/branch is something the person said to avoid."""
    total_w = contrib_sum = known_w = 0.0
    excluded = False
    matched, unmatched, unknown = [], [], []
    notes: list[tuple[float, str]] = []
    for p in profile.preferences:
        w = WEIGHT[p.weight]
        if w == 0 or not (p.want or p.avoid) or p.feature == "logistics.distance":
            continue
        meta = feature(p.feature)
        e = best.get(p.feature)
        m = agreement(e, p.want, p.feature, p.avoid)
        total_w += w
        label = meta["label"]
        if m == 0:
            unknown.append(p.feature)
            if p.weight == "dealbreaker":
                contrib_sum += -0.1 * w
            continue
        known_w += w
        if p.feature in IDENTITY_FILTER and p.avoid and m < 0:
            excluded = True   # the only hard filter (denomination level)
        s = strength(e, denom_conf, variability(p.feature))
        c = w * s * m
        contrib_sum += c
        (matched if m > 0 else unmatched).append(p.feature)
        verb = "avoid" if (m < 0 and p.avoid) else ""
        text = f"{'✓' if m > 0 else '✗'} {label}: {e.value.replace('_', ' ')}"
        notes.append((c, f"{text} — {_phrase(e, denom_label)}" + (" (you'd rather avoid this)" if verb else "")))
    score = 50.0 if total_w == 0 else 50 + 50 * contrib_sum / total_w
    if distance_miles is not None and profile.max_miles:
        start = 0.6 * profile.max_miles
        if distance_miles > start:
            score -= min(10.0, 10 * (distance_miles - start) / (0.4 * profile.max_miles))
    known_share = 1.0 if total_w == 0 else known_w / total_w
    pos = sorted([n for n in notes if n[0] > 0], key=lambda n: -n[0])[:2]
    neg = sorted([n for n in notes if n[0] <= 0], key=lambda n: n[0])[:1]
    why = [t for _, t in pos + neg]
    if unknown:   # U28: one short line instead of a line per unknown
        why.append(f"{len(unknown)} thing{'s' if len(unknown) != 1 else ''} to check later")
    score = round(max(0.0, min(100.0, score)), 1)
    return MatchResult(church_id=church_id, score=score, excluded=excluded, matched=matched, unmatched=unmatched,
                       unknown=unknown, why=why[:4], fit=fit_label(score, known_share) if total_w else "unknown",
                       known_share=round(known_share, 2))


def score(church: Church, profile: PreferenceProfile, kb=None) -> MatchResult:
    """Score one church. Church evidence beats denominational priors; priors never exclude."""
    did = church.denomination.denomination_id
    best: dict[str, Evidence] = {}
    for p in profile.preferences:
        if p.feature in ("identity.tradition", "identity.branch") and kb is not None and did:
            e = best_evidence(church.evidence, p.feature) or kb.prior(did, p.feature)
            if e is not None:
                best[p.feature] = e
            continue
        if p.feature == "identity.denomination" and did:
            best[p.feature] = Evidence(feature=p.feature, value=did, tier="prior", how="prior", source_kind="denomination_kb",
                                       note=f"identified as {church.denomination.label}")
            continue
        e = best_evidence(church.evidence, p.feature)
        if e is None and kb is not None and did:
            e = kb.prior(did, p.feature)
        if e is not None:
            best[p.feature] = e
    # Explicit positive affiliation requests constrain the candidate list only when affiliation is verified.
    identity_wants = [p for p in profile.preferences if p.feature in IDENTITY_FILTER and p.want and p.weight != "dont_care" and p.conf >= 0.8]
    result_excluded = False
    if church.denomination.confidence >= 0.8 and church.denomination.method != "unknown":
        for pref in identity_wants:
            ev = best.get(pref.feature)
            if ev and ev.value.lower() not in UNKNOWN_VALUES and agreement(ev,pref.want,pref.feature)<0:
                result_excluded = True
    var = (lambda f: kb.variability(did, f)) if (kb is not None and did) else (lambda f: 0.3)
    result = score_evidence(church.church_id, profile, best, denom_conf=church.denomination.confidence, variability=var,
                          denom_label=church.denomination.label, distance_miles=church.distance_miles)

    if result_excluded:
        result = result.model_copy(update={"excluded":True})
    return result
