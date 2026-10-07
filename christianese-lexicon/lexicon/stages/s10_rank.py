"""10 rank: merge every signal into one table and rank terms for drafting.

ambiguity_raw = 0.35*polysemy + 0.25*drift + 0.25*contestedness + 0.15*negation_rate
                (missing signals count as 0)
score = w.seeker*z(log1p seeker demand) + w.keyness*z(z_english)
      + w.ambiguity*z(ambiguity_raw) + w.seed*is_seed
"""
from __future__ import annotations

import logging
import math

from ..util import Ctx, read_json, read_jsonl, write_jsonl, zscores

log = logging.getLogger("lexicon.rank")
NAME = "10_rank"


def _by_term(path):
    return {r["term"]: r for r in read_jsonl(path)}


def run(ctx: Ctx) -> dict:
    w = ctx.cfg["rank"]["weights"]
    W = ctx.work
    short = _by_term(W / "05_shortlist" / "shortlist.jsonl")
    cands = _by_term(W / "02_counts" / "candidates.jsonl")
    key = _by_term(W / "04_keyness" / "keyness.jsonl")
    occ = _by_term(W / "06_contexts" / "occurrences.jsonl")
    poly = _by_term(W / "07_polysemy" / "polysemy.jsonl")
    stance = _by_term(W / "08_stance" / "stance.jsonl")
    ground = _by_term(W / "09_grounding" / "grounding.jsonl")
    phrases = read_json(W / "03_seeker" / "phrases.json")

    rows = []
    for t, s in short.items():
        o, p, st, k = occ.get(t, {}), poly.get(t, {}), stance.get(t, {}), key.get(t, {})
        ph = phrases.get(t, {})
        amb = (0.35 * (p.get("polysemy") or 0) + 0.25 * (p.get("drift") or 0)
               + 0.25 * (st.get("contestedness") or 0) + 0.15 * (st.get("negation_rate") or 0))
        rows.append({
            "term": t, "reason": s["reason"], "seed": s["reason"] == "seed" or cands.get(t, {}).get("seed", False),
            "count": o.get("count", 0), "seeker_count": o.get("seeker", 0), "church_count": o.get("church", 0),
            "seeker_requests": ph.get("count", 0), "seeker_want": ph.get("want", 0), "seeker_avoid": ph.get("avoid", 0),
            "seeker_category": ph.get("category"),
            "z_english": k.get("z_english", 0.0), "top_tradition": k.get("top_tradition"),
            "top_tradition_z": k.get("top_tradition_z", 0.0),
            "polysemy": p.get("polysemy"), "drift": p.get("drift"), "nmi": p.get("nmi"),
            "contestedness": st.get("contestedness"), "tradition_split": st.get("tradition_split"),
            "negation_rate": st.get("negation_rate", 0.0), "stance_counts": st.get("stance_counts", {}),
            "observables": ground.get(t, {}).get("observables", []),
            "by_tradition": o.get("by_tradition", {}),
            "ambiguity_raw": round(amb, 4),
            "_demand": math.log1p(ph.get("count", 0) + o.get("seeker", 0)),
        })
    zd = zscores([r["_demand"] for r in rows])
    zk = zscores([r["z_english"] for r in rows])
    za = zscores([r["ambiguity_raw"] for r in rows])
    for r, a, b, c in zip(rows, zd, zk, za):
        r["score"] = round(w["seeker"] * a + w["keyness"] * b + w["ambiguity"] * c + w["seed"] * float(r["seed"]), 4)
        del r["_demand"]
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    write_jsonl(ctx.stage_dir(NAME) / "ranked.jsonl", rows)
    summary = {"ranked": len(rows), "top10": [r["term"] for r in rows[:10]]}
    log.info("rank: %s", summary)
    return summary
