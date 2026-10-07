"""Shared feature vocabulary (contracts/features.yaml). The only reader of that file."""
from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlparse

import yaml

from .config import get_settings
from .models import Evidence

ASK_LEVELS = ("core", "standard", "if_raised", "advanced", "never")
_COUNT = re.compile(r"(\d+)\s*(?:of|/)\s*(\d+)")


@lru_cache
def load_features() -> dict:
    with open(get_settings().features_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def all_features() -> dict:
    return load_features()["features"]


def feature(fid: str) -> dict:
    try:
        return all_features()[fid]
    except KeyError:
        raise KeyError(f"unknown feature id {fid!r} (not in features.yaml)") from None


def stage0_questions(level: str) -> list[dict]:
    """Features at this ask level that HAVE a `question`, in YAML order (ladder fills all women.*)."""
    if level not in ASK_LEVELS:
        raise ValueError(level)
    return [{"id": k, **v} for k, v in all_features().items() if v.get("ask") == level and v.get("question")]


def data_points(note: str) -> int:
    """Denominator of 'N of M' in an evidence note (number of observations), else 0."""
    m = _COUNT.search(note or "")
    return int(m.group(2)) if m else 0


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_settled(evidence: list[Evidence], fid: str) -> bool:
    ev = [e for e in evidence if e.feature == fid and e.value not in ("unknown",)]
    if any(e.tier == "A" and e.quote for e in ev):
        return True
    if len({_domain(e.url) for e in ev if e.tier == "B" and e.url}) >= 2:
        return True
    return any(e.tier == "D" and e.how == "observed" and data_points(e.note) >= 5 for e in ev)


def settled_open(evidence: list[Evidence], feature_ids: list[str]) -> tuple[list[str], list[str]]:
    """Apply features.yaml settled_rule. The only implementation (Stage 2, Stage 3, report)."""
    settled = [f for f in feature_ids if is_settled(evidence, f)]
    return settled, [f for f in feature_ids if f not in settled]
