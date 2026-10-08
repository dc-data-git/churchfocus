"""Shared schemas (INTERFACES.md §3). Change here only after updating INTERFACES.md."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Weight = Literal["dealbreaker", "important", "nice_to_have", "dont_care"]
Tier = Literal["A", "B", "C", "D", "prior"]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Preference(BaseModel):
    feature: str
    want: list[str] = Field(default_factory=list)
    weight: Weight = "important"
    said: str = ""
    avoid: list[str] = Field(default_factory=list)   # v2 (D27/D28): values the person does not want
    strength: float = 0.5                            # 0..1 from the memory log
    conf: float = 1.0                                # 0..1 how sure we are


class PreferenceProfile(BaseModel):
    session_id: str
    for_whom: Literal["self", "other"] = "self"
    origin: dict = Field(default_factory=dict)
    max_miles: float = 15
    preferences: list[Preference] = Field(default_factory=list)
    likely_denominations: list[str] = Field(default_factory=list)
    confirmed: bool = False


class Evidence(BaseModel):
    feature: str
    value: str
    tier: Tier
    quote: str = ""
    url: str = ""
    source_kind: str
    how: Literal["stated", "observed", "inferred", "prior"]
    checked_at: datetime = Field(default_factory=now)
    note: str = ""


class DenomGuess(BaseModel):
    denomination_id: str | None = None
    label: str = "Unknown"
    confidence: float = 0.0
    method: Literal["name", "locator", "network", "website", "inferred", "unknown"] = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)


class Church(BaseModel):
    church_id: str
    name: str
    address: str = ""
    lat: float = 0.0
    lng: float = 0.0
    website: str | None = None
    phone: str | None = None
    distance_miles: float = 0.0
    denomination: DenomGuess = Field(default_factory=DenomGuess)
    evidence: list[Evidence] = Field(default_factory=list)
    stage_done: int = 1


Fit = Literal["strong", "possible", "unknown", "unlikely", "poor"]


class MatchResult(BaseModel):
    church_id: str
    score: float
    excluded: bool
    fit: Fit = "unknown"          # v2 (D31)
    known_share: float = 0.0      # share of preference weight with any evidence/prior
    matched: list[str] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)


class StepLog(BaseModel):
    ts: datetime = Field(default_factory=now)
    session_id: str
    church_id: str | None = None
    stage: int
    step: int
    action: str
    why: str
    input: dict = Field(default_factory=dict)
    result_summary: str = ""
    features_moved: list[str] = Field(default_factory=list)
    tokens: int = 0
    cost_usd: float = 0
    ms: int = 0


class Escalation(BaseModel):
    reason: Literal["pastoral_or_crisis", "dealbreaker_conflict", "low_denom_confidence", "budget_exhausted"]
    message: str
    questions_to_ask: list[str] = Field(default_factory=list)


class ChurchReport(BaseModel):
    church: Church
    profile_session: str
    match: MatchResult
    settled: list[str] = Field(default_factory=list)
    open: list[str] = Field(default_factory=list)
    stated_vs_observed: list[dict] = Field(default_factory=list)
    sermons_analysed: int = 0
    escalations: list[Escalation] = Field(default_factory=list)
    questions_for_visit: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=now)
    log_path: str = ""


class MemoryOp(BaseModel):
    """One line of the per-session memory log (D27, REDESIGN §2). Append-only."""
    t: int = 0
    op: Literal["assert", "revise", "confirm", "retract"] = "assert"
    key: str
    val: str | list[str] | dict = ""
    stance: Literal["want", "avoid", "neutral"] = "want"
    strength: float = 0.5
    conf: float = 0.5
    src: Literal["stated", "inferred", "confirmed", "user_edit", "lexicon"] = "inferred"
    ev: str = ""
    why: str = ""
    supersedes: int | None = None
