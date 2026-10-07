"""05 shortlist: cheap score -> the N terms that get the expensive analyses.

cheap score = z(log1p seeker demand) + z(keyness vs English) + seed bonus,
where seeker demand = times seekers asked for it (stage 03) + raw seeker-side count.

Filters (seed terms are exempt from all of them):
- generic: listed in generic_terms (churchy but needs no translation: "church", "sunday")
- common English: general-English frequency above max_ref_freq ("want", "thanks")
- subsumed: >= subsume_ratio of its uses sit inside one longer candidate
  ("call" inside "altar call"); the longer phrase is kept instead
All seed terms and frequent seeker phrases (>= 2 requests) are always included.
"""
from __future__ import annotations

import logging
import math

from wordfreq import word_frequency

from ..util import Ctx, read_json, read_jsonl, read_list, write_jsonl, zscores

log = logging.getLogger("lexicon.shortlist")
NAME = "05_shortlist"


def subsumed_terms(cands: dict[str, dict], ratio: float) -> dict[str, str]:
    """term -> longer phrase that accounts for >= ratio of its occurrences."""
    by_tok: dict[str, list[str]] = {}
    for t in cands:
        if " " in t:
            for tok in set(t.split()):
                by_tok.setdefault(tok, []).append(t)
    out = {}
    for t, c in cands.items():
        if c["count"] == 0:
            continue
        toks = t.split()
        pool = by_tok.get(toks[0], [])
        best = None
        for longer in pool:
            lt = longer.split()
            if len(lt) <= len(toks):
                continue
            if any(lt[i:i + len(toks)] == toks for i in range(len(lt) - len(toks) + 1)):
                if cands[longer]["count"] >= ratio * c["count"] and (best is None or cands[longer]["count"] > cands[best]["count"]):
                    best = longer
        if best:
            out[t] = best
    return out


def run(ctx: Ctx) -> dict:
    sc = ctx.cfg["shortlist"]
    size = sc["size"]
    max_ref = sc.get("max_ref_freq", 1.5e-4)
    ratio = sc.get("subsume_ratio", 0.8)
    generic = set(read_list(ctx.path("generic_terms"))) if "generic_terms" in ctx.cfg else set()
    if ctx.cfg.get("filler_terms"):  # spoken filler and podcast/video boilerplate (transcribed corpora)
        generic |= set(read_list(ctx.path("filler_terms")))
    cands = {c["term"]: c for c in read_jsonl(ctx.work / "02_counts" / "candidates.jsonl")}
    key = {k["term"]: k for k in read_jsonl(ctx.work / "04_keyness" / "keyness.jsonl")}
    phrases = read_json(ctx.work / "03_seeker" / "phrases.json")
    subsumed = subsumed_terms(cands, ratio)

    excluded = {"generic": 0, "common_english": 0, "subsumed": 0, "one_source": 0}
    max_share = ctx.cfg["counts"].get("max_source_share", 1.0)

    def eligible(t: str) -> bool:
        if cands.get(t, {}).get("seed"):
            return True
        if t in generic:
            excluded["generic"] += 1
            return False
        if key.get(t, {}).get("ref_freq", 0) > max_ref:
            excluded["common_english"] += 1
            return False
        if t in subsumed:
            excluded["subsumed"] += 1
            return False
        if cands[t].get("top_source_share", 0) > max_share:  # one show's topic or catchphrase
            excluded["one_source"] += 1
            return False
        return True

    terms = [t for t in cands if eligible(t)]
    demand = [math.log1p(phrases.get(t, {}).get("count", 0) + cands[t]["seeker"]) for t in terms]
    keyz = [key.get(t, {}).get("z_english", 0.0) for t in terms]
    zd, zk = zscores(demand), zscores(keyz)
    scored = sorted(((a + b + (1.0 if cands[t]["seed"] else 0.0), t) for t, a, b in zip(terms, zd, zk)), reverse=True)

    chosen: dict[str, str] = {t: "seed" for t in terms if cands[t]["seed"]}
    for p, v in phrases.items():
        if (v["count"] >= 2 and p not in chosen and p not in generic and p not in subsumed
                and word_frequency(p, "en") <= max_ref):
            chosen[p] = "seeker_phrase"
    for _, t in scored:
        if len(chosen) >= size:
            break
        chosen.setdefault(t, "score")

    rows = [{"term": t, "reason": r} for t, r in chosen.items()]
    write_jsonl(ctx.stage_dir(NAME) / "shortlist.jsonl", rows)
    summary = {"shortlist": len(rows), "excluded": excluded,
               "by_reason": {r: sum(1 for x in rows if x["reason"] == r) for r in ("seed", "seeker_phrase", "score")}}
    log.info("shortlist: %s", summary)
    return summary
