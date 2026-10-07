"""Shared evidence rules (features.yaml `rules`): allowed values and the marriage rule.
Used by Stage 2 extraction and the Stage 3 record_evidence / analyse_sermons tools so both enforce the same thing."""
from __future__ import annotations

import re

from app.features import all_features

ALWAYS_ALLOWED = {"unstated", "unknown"}   # features.yaml: always allowed in addition to `values`

MARRIAGE_DEF_RE = re.compile(
    r"one man.{0,40}one woman|a man and a woman|man and woman|husband and wife|same[- ]sex marriage|"
    r"marriage between (any )?two (people|persons|adults)|marriage equality|all couples",
    re.I,
)
INCLUSION_WORDS_RE = re.compile(
    r"\b(member(s|ship)?|lead(er|ers|ership)?|serve|serving|ordain\w*|clergy|elders?|deacons?|staff|"
    r"welcome[sd]?|fully (include|included|inclusive)|affirming congregation|reconciling|open and affirming)\b",
    re.I,
)


def allowed_values(fid: str) -> set[str]:
    return {str(v) for v in all_features()[fid]["values"]} | ALWAYS_ALLOWED


def value_ok(fid: str, value: str) -> bool:
    return fid in all_features() and str(value) in allowed_values(fid)


def marriage_rule_violation(fid: str, value: str, quote: str) -> str | None:
    """Return a reason string if this evidence breaks the marriage rule, else None.
    - A marriage definition settles lgbtq.marriage only; it can never set lgbtq.inclusion.
    - lgbtq.marriage = traditional needs a quote that actually defines marriage."""
    q = quote or ""
    if fid == "lgbtq.inclusion" and value not in ALWAYS_ALLOWED:
        if MARRIAGE_DEF_RE.search(q) and not INCLUSION_WORDS_RE.search(q):
            return "marriage rule: a marriage definition does not settle LGBTQ membership/leadership"
    if fid == "lgbtq.marriage" and value == "traditional" and q and not MARRIAGE_DEF_RE.search(q):
        return "marriage rule: 'traditional' needs a quote that defines marriage"
    return None
