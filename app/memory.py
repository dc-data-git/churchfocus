"""Per-session user memory (D27, REDESIGN §2): append-only change log -> current beliefs -> PreferenceProfile.

The log is the record of *why* the app believes something about the person. Nothing is overwritten: revisions point
at what they supersede. The scoring side reads only to_profile(), which applies fixed (formulaic) rules.
"""
from __future__ import annotations

import json
import threading

from app import db
from app.features import all_features, feature
from app.models import MemoryOp, Preference, PreferenceProfile

_lock = threading.Lock()
_versions: dict[str, int] = {}
_table_versions: dict[str, int] = {}
NON_FEATURE_KEYS = {"location", "for_whom", "denomination"}


def version(session_id: str) -> int:
    return db.session_state(session_id)["memory_version"]


def table_version(session_id: str) -> int:
    return db.session_state(session_id)["table_version"]


def bump_table(session_id: str) -> None:
    db.state_bump(session_id, "table_version")


def log(session_id: str) -> list[MemoryOp]:
    return [MemoryOp.model_validate_json(j) for j in db.memory_rows(session_id)]


def _valid(op: MemoryOp) -> str | None:
    """Return a reason if the op must be dropped (unknown key / value). Values are kept as given otherwise."""
    if op.key in NON_FEATURE_KEYS:
        return None
    feats = all_features()
    if op.key not in feats:
        return f"unknown feature {op.key!r}"
    allowed = {str(v) for v in feats[op.key]["values"]}
    placeholder = any(v in allowed for v in ("miles", "schedule", "language_list", "ministry_list", "topic_list",
                                             "denomination_id", "network_name", "stated_position_text"))
    vals = op.val if isinstance(op.val, list) else [op.val]
    if not placeholder and op.op != "retract" and not all(str(v) in allowed for v in vals):
        return f"value {op.val!r} not allowed for {op.key}"
    return None


def append(session_id: str, ops: list[MemoryOp]) -> list[str]:
    """Validate and append. Returns reasons for dropped ops (also logged by the caller)."""
    good, dropped = [], []
    for op in ops:
        why = _valid(op)
        if why:
            dropped.append(why)
            continue
        good.append(op.model_dump_json())
    if good:
        with _lock:
            db.memory_append(session_id, good)
            db.state_bump(session_id, "memory_version")
            bump_table(session_id)
    return dropped


def current(session_id: str) -> dict[str, MemoryOp]:
    """Latest non-retracted op per key (for location: per key 'location')."""
    out: dict[str, MemoryOp] = {}
    for op in log(session_id):
        if op.op == "retract":
            out.pop(op.key, None)
        else:
            out[op.key] = op
    return out


def _weight(strength: float, conf: float, sensitive: bool, src: str) -> str:
    steps = ["dont_care", "nice_to_have", "important", "dealbreaker"]
    i = 3 if strength >= 0.8 else 2 if strength >= 0.5 else 1 if strength > 0.15 else 0
    if conf < 0.5:
        i = max(0, i - 1)
    if sensitive and src in ("inferred", "lexicon"):
        i = min(i, 1)   # unconfirmed guesses on sensitive topics barely count (D27 / guardrail G2)
    return steps[i]


def location(session_id: str) -> dict | None:
    op = current(session_id).get("location")
    return op.val if op and isinstance(op.val, dict) else None


def to_profile(session_id: str) -> PreferenceProfile:
    cur = current(session_id)
    prof = PreferenceProfile(session_id=session_id)
    loc = location(session_id)
    if loc:
        prof.origin = {k: loc[k] for k in ("text", "lat", "lng") if k in loc}
        if loc.get("limit_miles"):
            prof.max_miles = float(loc["limit_miles"])
    fw = cur.get("for_whom")
    if fw and fw.val in ("self", "other"):
        prof.for_whom = fw.val
    feats = all_features()
    for key, op in cur.items():
        if key in NON_FEATURE_KEYS and key != "denomination":
            continue
        fid = "identity.denomination" if key == "denomination" else key
        if fid not in feats or op.stance == "neutral":
            continue
        vals = [str(v) for v in (op.val if isinstance(op.val, list) else [op.val])]
        w = _weight(op.strength, op.conf, bool(feats[fid].get("sensitive")), op.src)
        pref = Preference(feature=fid, weight=w, said=op.ev[:200], strength=op.strength, conf=op.conf,
                          want=vals if op.stance == "want" else [], avoid=vals if op.stance == "avoid" else [])
        prof.preferences = [p for p in prof.preferences if p.feature != fid] + [pref]
    return prof


def _label(op: MemoryOp) -> str:
    if op.key == "location":
        v = op.val if isinstance(op.val, dict) else {}
        lim = f", within {v.get('limit_miles'):g} miles" if v.get("limit_miles") else ""
        return f"Starting from {v.get('text', '?')}{lim}"
    if op.key == "for_whom":
        return "Looking for a church for someone else" if op.val == "other" else "Looking for a church for yourself"
    fid = "identity.denomination" if op.key == "denomination" else op.key
    try:
        label = feature(fid)["label"]
    except KeyError:
        label = op.key
    vals = op.val if isinstance(op.val, list) else [op.val]
    if op.key == "denomination":
        from app.denom.kb import get_kb
        kb = get_kb()
        vals = [kb.groups[v]["name"] if v in kb.groups else v for v in vals]
    v = ", ".join(str(x).replace("_", " ") for x in vals)
    strength = "a must" if op.strength >= 0.8 else "important" if op.strength >= 0.5 else "nice to have"
    if op.stance == "avoid":
        return f"{label}: would rather avoid {v} ({strength})"
    return f"{label}: {v} ({strength})"


def plain_summary(session_id: str) -> list[dict]:
    src_text = {"stated": "you said so", "confirmed": "you confirmed", "user_edit": "you edited this",
                "inferred": "my guess from what you said", "lexicon": "my guess from your wording"}
    out = []
    for key, op in current(session_id).items():
        if op.stance == "neutral" and key not in ("location", "for_whom"):
            continue
        out.append({"key": key, "text": _label(op), "src": op.src, "src_text": src_text.get(op.src, op.src),
                    "conf": op.conf, "ev": op.ev})
    order = {"location": 0, "for_whom": 1, "denomination": 2}
    return sorted(out, key=lambda x: (order.get(x["key"], 3), -x["conf"]))


def next_turn(session_id: str) -> int:
    return max(max((op.t for op in log(session_id)), default=0) + 1, sum(m["role"] == "user" for m in db.messages_since(session_id)))


def user_edit(session_id: str, key: str, text: str):
    """The person edited an item in the About-you panel. The conversation module interprets the text into ops."""
    from app.stage0 import conversation
    return conversation.apply_user_edit(session_id, key, text)


def dump(session_id: str) -> str:
    """Whole log as JSONL (for the audit trail / build doc)."""
    return "\n".join(json.dumps(json.loads(j)) for j in db.memory_rows(session_id))


def context(session_id: str) -> dict:
    """History preserves corrections/reasons; current view alone drives active filters."""
    return {"current": {key: op.model_dump(mode="json") for key,op in current(session_id).items()},
            "history": [op.model_dump(mode="json") for op in log(session_id)]}
