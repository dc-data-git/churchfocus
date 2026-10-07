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


def agreement(e: Evidence | None, want: list[str], fid: str) -> int:
    """+1 matches, -1 conflicts, 0 unknown."""
    if e is None or e.value.strip().lower() in UNKNOWN_VALUES:
        return 0
    vals = [v.strip() for v in e.value.split(";") if v.strip()]
    if set(feature(fid)["values"]) & PLACEHOLDER:  # free-form values: loose text match
        return 1 if any(w.lower() in e.value.lower() for w in want) else -1
    return 1 if any(v in want for v in vals) else -1


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


def score_evidence(church_id: str, profile: PreferenceProfile, best: dict[str, Evidence], denom_conf: float = 0.0,
                   variability: Callable[[str], float] = lambda f: 0.3, denom_label: str = "its denomination",
                   distance_miles: float | None = None) -> MatchResult:
    total_w = contrib_sum = 0.0
    excluded = False
    matched, unmatched, unknown = [], [], []
    notes: list[tuple[float, str]] = []
    for p in profile.preferences:
        w = WEIGHT[p.weight]
        if w == 0 or not p.want or p.feature == "logistics.distance":
            continue
        meta = feature(p.feature)
        e = best.get(p.feature)
        m = agreement(e, p.want, p.feature)
        total_w += w
        label = meta["label"]
        if m == 0:
            unknown.append(p.feature)
            if p.weight == "dealbreaker":
                contrib_sum += -0.1 * w
                notes.append((-0.1 * w - 0.01, f"? {label}: unknown so far (a must-have for you) — ask on a visit"))
            continue
        s = strength(e, denom_conf, variability(p.feature))
        c = w * s * m
        contrib_sum += c
        (matched if m > 0 else unmatched).append(p.feature)
        notes.append((c, f"{'✓' if m > 0 else '✗'} {label}: {e.value.replace('_', ' ')} — {_phrase(e, denom_label)}"))
        if m < 0 and p.weight == "dealbreaker" and (e.tier in ("A", "B") or (e.tier == "D" and s >= 0.6)):
            excluded = True
    score = 50.0 if total_w == 0 else 50 + 50 * contrib_sum / total_w
    if distance_miles is not None and profile.max_miles:
        start = 0.6 * profile.max_miles
        if distance_miles > start:
            score -= min(10.0, 10 * (distance_miles - start) / (0.4 * profile.max_miles))
            notes.append((-0.001, f"Distance: {distance_miles:.1f} mi (your limit {profile.max_miles:g} mi)"))
    pos = sorted([n for n in notes if n[0] > 0], key=lambda n: -n[0])[:2]
    neg = sorted([n for n in notes if n[0] <= 0], key=lambda n: n[0])[:2]
    return MatchResult(church_id=church_id, score=round(max(0.0, min(100.0, score)), 1), excluded=excluded,
                       matched=matched, unmatched=unmatched, unknown=unknown, why=[t for _, t in pos + neg])


def score(church: Church, profile: PreferenceProfile, kb=None) -> MatchResult:
    """Score one church. Church evidence beats denominational priors; priors never exclude."""
    did = church.denomination.denomination_id
    best: dict[str, Evidence] = {}
    for p in profile.preferences:
        e = best_evidence(church.evidence, p.feature)
        if e is None and kb is not None and did:
            e = kb.prior(did, p.feature)
        if e is not None:
            best[p.feature] = e
    var = (lambda f: kb.variability(did, f)) if (kb is not None and did) else (lambda f: 0.3)
    return score_evidence(church.church_id, profile, best, denom_conf=church.denomination.confidence, variability=var,
                          denom_label=church.denomination.label, distance_miles=church.distance_miles)
