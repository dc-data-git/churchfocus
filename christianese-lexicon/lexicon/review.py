"""Human review loop, tiered by risk (full double review of everything is ~15 person-hours;
this is ~4-5).

Tiers (assigned automatically from the drafted entry and its statistics):
  A      contested / evaluative / politically coded terms, or polysemous terms whose meaning
         drifts across traditions (drift >= review.drift_threshold).
         Where neutrality matters most. Needs 2 independent reviewers (kappa is computed here).
  B      next review.tier_b_size terms by seeker demand. Needs 1 reviewer.
  audit  review.audit_size random terms from the rest. 1 reviewer. Gives an honest error
         estimate for everything nobody reviewed.
  C      everything else. Not reviewed; stays vetted=false (the app confirms meaning with the user).

Commands
  lexicon review plan                                  -> review/plan.csv (tier per term, counts)
  lexicon review export --reviewer NAME [--tiers A,B,audit]   -> review/review_<NAME>.csv
  lexicon review kappa A.csv B.csv                     agreement between two reviewers (tier A overlap)
  lexicon review apply A.csv [B.csv ...]               -> work/out/lexicon_reviewed.json + review/summary.json

Reviewer columns (y / n; notes free text):
  keep         worth having in the lexicon?
  types_ok     term_types right (polysemous / contested / ...)?
  features_ok  senses map to the right observable features?
  neutral_ok   every position described fairly, without ranking?
"""
from __future__ import annotations

import csv
import datetime as dt
import random
from pathlib import Path

from .util import Ctx, read_json, read_jsonl, write_json

COLS = ["keep", "types_ok", "features_ok", "neutral_ok"]
REQUIRED = {"A": 2, "B": 1, "audit": 1}
HIGH_RISK_TYPES = {"contested", "evaluative", "politically_coded"}
DEFAULTS = {"drift_threshold": 0.3, "tier_b_size": 50, "audit_size": 20, "seed": 13}


def _rcfg(ctx: Ctx) -> dict:
    return {**DEFAULTS, **(ctx.cfg.get("review") or {})}


def _draft(ctx: Ctx) -> list[dict]:
    return read_json(ctx.work / "11_draft" / "lexicon_draft.json")


def assign_tiers(entries: list[dict], rc: dict) -> dict[str, str]:
    tiers: dict[str, str] = {}
    for e in entries:
        types = set(e["term_types"])
        drift = (e.get("evidence_stats") or {}).get("drift") or 0
        if types & HIGH_RISK_TYPES or ("polysemous" in types and drift >= rc["drift_threshold"]):
            tiers[e["term"]] = "A"
    rest = [e for e in entries if e["term"] not in tiers]
    demand = lambda e: ((e.get("evidence_stats") or {}).get("seeker_want", 0)
                        + (e.get("evidence_stats") or {}).get("seeker_avoid", 0))
    rest.sort(key=lambda e: (-demand(e), (e.get("evidence_stats") or {}).get("rank", 1e9)))
    for e in rest[: rc["tier_b_size"]]:
        tiers[e["term"]] = "B"
    pool = sorted(e["term"] for e in rest[rc["tier_b_size"]:])
    for t in random.Random(rc["seed"]).sample(pool, min(rc["audit_size"], len(pool))):
        tiers[t] = "audit"
    for e in entries:
        tiers.setdefault(e["term"], "C")
    return tiers


def plan(ctx: Ctx) -> tuple[Path, dict]:
    entries = _draft(ctx)
    tiers = assign_tiers(entries, _rcfg(ctx))
    out = ctx.root / "review" / "plan.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["term", "tier", "reviewers_needed", "term_types", "category"])
        for e in sorted(entries, key=lambda e: ("A", "B", "audit", "C").index(tiers[e["term"]])):
            t = tiers[e["term"]]
            w.writerow([e["term"], t, REQUIRED.get(t, 0), ", ".join(e["term_types"]), e["category"]])
    counts = {t: sum(1 for v in tiers.values() if v == t) for t in ("A", "B", "audit", "C")}
    minutes = 3 * (2 * counts["A"]) + 1.5 * counts["B"] + 1.5 * counts["audit"]
    return out, {**counts, "estimated_person_hours": round(minutes / 60, 1)}


def export(ctx: Ctx, reviewer: str, tiers_wanted: list[str] | None = None) -> Path:
    tiers_wanted = tiers_wanted or ["A", "B", "audit"]
    entries = _draft(ctx)
    tiers = assign_tiers(entries, _rcfg(ctx))
    ctxs = {r["term"]: r["contexts"] for r in read_jsonl(ctx.work / "06_contexts" / "contexts.jsonl")}
    out = ctx.root / "review" / f"review_{reviewer}.csv"
    out.parent.mkdir(exist_ok=True)
    order = ("A", "B", "audit", "C")
    rows = sorted((e for e in entries if tiers[e["term"]] in tiers_wanted),
                  key=lambda e: order.index(tiers[e["term"]]))
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tier", "term", "category", "term_types", "senses", "disambiguation_question", "neutral_options",
                    "example_contexts", *COLS, "notes"])
        for e in rows:
            senses = " || ".join(
                f"{s['gloss']} [{', '.join(s['traditions'])}] -> " + ", ".join(f"{x['feature']}={x['value']}" for x in s["features"])
                for s in e["senses"])
            ex = " || ".join(c["text"] for c in ctxs.get(e["term"], [])[:3])
            w.writerow([tiers[e["term"]], e["term"], e["category"], ", ".join(e["term_types"]), senses,
                        e["disambiguation_question"], " | ".join(e["neutral_options"]), ex, "", "", "", "", ""])
    return out


def _read(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return {r["term"]: r for r in csv.DictReader(f)}


def _yn(v: str) -> int | None:
    v = (v or "").strip().lower()
    return 1 if v.startswith("y") else 0 if v.startswith("n") else None


def cohen_kappa(a: list[int], b: list[int]) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def kappa(path_a: str, path_b: str) -> dict:
    A, B = _read(path_a), _read(path_b)
    res = {}
    for col in COLS:
        pairs = [(_yn(A[t][col]), _yn(B[t][col])) for t in A if t in B]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        a, b = [x for x, _ in pairs], [y for _, y in pairs]
        res[col] = {"n": len(pairs), "agreement": round(sum(x == y for x, y in pairs) / len(pairs), 3) if pairs else None,
                    "kappa": round(cohen_kappa(a, b), 3) if pairs else None}
        if pairs and (len(set(a)) == 1 or len(set(b)) == 1):
            res[col]["warning"] = "one reviewer gave the same answer to every item; kappa is not meaningful"
    return res


def apply(ctx: Ctx, paths: list[str]) -> Path:
    """Merge review sheets. Output keeps every entry except ones reviewers rejected:
    vetted=true only when the tier's required number of reviewers all said keep=y and neutral_ok=y."""
    entries = _draft(ctx)
    tiers = assign_tiers(entries, _rcfg(ctx))
    sheets = [(Path(p).stem.replace("review_", ""), _read(p)) for p in paths]
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out_entries, rejected, under_reviewed = [], [], []
    audit_flags = []
    for e in entries:
        t = tiers[e["term"]]
        votes = [(n, s[e["term"]]) for n, s in sheets
                 if e["term"] in s and _yn(s[e["term"]]["keep"]) is not None]
        e = dict(e)
        prov = {**e["provenance"], "review_tier": t, "reviewers": [n for n, _ in votes]}
        if t == "audit" and votes:
            audit_flags.append(any(_yn(v[c]) == 0 for _, v in votes for c in COLS))
        if votes and any(_yn(v["keep"]) == 0 for _, v in votes):
            rejected.append({"term": e["term"], "tier": t, "notes": [v.get("notes", "") for _, v in votes]})
            continue
        ok = votes and all(_yn(v["neutral_ok"]) == 1 for _, v in votes)
        enough = len(votes) >= REQUIRED.get(t, 99)
        if t in REQUIRED and not enough:
            under_reviewed.append({"term": e["term"], "tier": t, "have": len(votes), "need": REQUIRED[t]})
        prov["vetted"] = bool(ok and enough)
        if prov["vetted"]:
            prov["vetted_at"] = now
        notes = [v["notes"] for _, v in votes if v.get("notes")]
        if notes:
            prov["review_notes"] = notes
        e["provenance"] = prov
        out_entries.append(e)
    out = ctx.work / "out" / "lexicon_reviewed.json"
    write_json(out, {"schema_version": "lexicon.v1", "reviewed_at": now, "entries": out_entries})
    n_audit = len(audit_flags)
    summary = {
        "entries_out": len(out_entries),
        "vetted": sum(1 for e in out_entries if e["provenance"]["vetted"]),
        "rejected_by_reviewers": rejected,
        "under_reviewed": under_reviewed,
        "audit": {"reviewed": n_audit, "with_any_error": sum(audit_flags),
                  "error_rate": round(sum(audit_flags) / n_audit, 3) if n_audit else None,
                  "meaning": "estimated share of UNREVIEWED (tier C) entries with at least one error"},
    }
    write_json(ctx.root / "review" / "summary.json", summary)
    return out
