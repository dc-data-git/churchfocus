"""02 counts: n-gram counts by side and tradition; phrase cohesion (NPMI); candidates.

Holdout seeker docs are excluded so the coverage eval stays honest.

Source dispersion: each term also records how many distinct sources (a podcast show, a
YouTube channel, a website) use it, overall and per tradition. A term that only one or two
sources use is usually that source's current topic or catchphrase, not shared vocabulary,
so non-seed candidates need counts.min_sources sources (0 turns the filter off).
"""
from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict

from ..text import is_content_ngram, ngrams, tokenize
from ..util import Ctx, read_list, write_json, write_jsonl
from .s01_ingest import load_docs

log = logging.getLogger("lexicon.counts")
NAME = "02_counts"


def run(ctx: Ctx) -> dict:
    c = ctx.cfg["counts"]
    max_n, min_count, min_npmi = c["max_ngram"], c["min_count"], c["min_npmi"]
    seeds = set(read_list(ctx.path("seed_terms")))
    seed_toks = {" ".join(tokenize(s)) for s in seeds}

    total = Counter()                       # n-gram -> count
    side_ct = {"seeker": Counter(), "church": Counter()}
    trad_ct: dict[str, Counter] = defaultdict(Counter)
    doc_freq = Counter()
    tok_totals = Counter()                  # per side / tradition token totals
    n_docs = Counter()
    order_totals = Counter()                # total n-grams per order
    src_mask: dict[str, int] = defaultdict(int)  # n-gram -> bitset of sources using it
    src_id: dict[str, int] = {}
    src_trad: dict[str, str] = {}

    for doc in load_docs(ctx):
        toks = tokenize(doc["text"])
        side, trad = doc["side"], doc["tradition"]
        src = doc.get("source") or "unknown"
        bit = 1 << src_id.setdefault(src, len(src_id))
        if trad:
            src_trad.setdefault(src, trad)
        tok_totals["all"] += len(toks)
        tok_totals[side] += len(toks)
        n_docs[side] += 1
        if trad and side == "church":
            tok_totals[f"t:{trad}"] += len(toks)
            n_docs[f"t:{trad}"] += 1
        seen = set()
        for n in range(1, max_n + 1):
            for g in ngrams(toks, n):
                total[g] += 1
                order_totals[n] += 1
                side_ct[side][g] += 1
                if trad and side == "church":
                    trad_ct[trad][g] += 1
                seen.add(g)
        doc_freq.update(seen)
        for g in seen:
            src_mask[g] |= bit

    def npmi(g: str) -> float:
        parts = g.split()
        if len(parts) == 1:
            return 1.0
        n = len(parts)
        p_g = total[g] / max(order_totals[n], 1)
        p_parts = 1.0
        for p in parts:
            p_parts *= total[p] / max(order_totals[1], 1)
        if p_g <= 0 or p_parts <= 0:
            return -1.0
        return math.log(p_g / p_parts) / -math.log(p_g)

    min_sources = c.get("min_sources", 0)
    trad_mask: dict[str, int] = defaultdict(int)
    for src, i in src_id.items():
        if src in src_trad:
            trad_mask[src_trad[src]] |= 1 << i

    def n_src(mask: int) -> int:
        return bin(mask).count("1")

    cands, dropped_sources = [], 0
    for g, ct in total.items():
        is_seed = g in seed_toks
        if not is_seed:
            if ct < min_count or not is_content_ngram(g):
                continue
            if min_sources and n_src(src_mask[g]) < min_sources:
                dropped_sources += 1
                continue
        score = npmi(g)
        if not is_seed and len(g.split()) > 1 and score < min_npmi:
            continue
        cands.append({
            "term": g, "n": len(g.split()), "count": ct, "npmi": round(score, 4),
            "seeker": side_ct["seeker"][g], "church": side_ct["church"][g], "doc_freq": doc_freq[g],
            "by_tradition": {t: tc[g] for t, tc in trad_ct.items() if tc[g]},
            "seed": is_seed,
            "n_sources": n_src(src_mask[g]),
            "tradition_sources": {t: k for t, m in trad_mask.items() if (k := n_src(src_mask[g] & m))},
        })
    # second pass, candidates only: how concentrated is each term in its single biggest source?
    # ("john the baptist": 68 of 84 uses from one show's series on John -> share 0.81)
    by_term = {x["term"]: x for x in cands}
    per_src: dict[str, Counter] = defaultdict(Counter)
    for doc in load_docs(ctx):
        toks = tokenize(doc["text"])
        src = doc.get("source") or "unknown"
        for n in range(1, max_n + 1):
            for g in ngrams(toks, n):
                if g in by_term:
                    per_src[g][src] += 1
    for g, x in by_term.items():
        sc = per_src.get(g)
        x["top_source_share"] = round(max(sc.values()) / sum(sc.values()), 3) if sc else 0.0
        tshare = {}
        for t in x["tradition_sources"]:
            vals = [v for src, v in sc.items() if src_trad.get(src) == t] if sc else []
            if vals:
                tshare[t] = round(max(vals) / sum(vals), 3)
        x["tradition_top_share"] = tshare

    # seeds absent from the corpus are still carried forward
    present = {x["term"] for x in cands}
    for s in seed_toks - present:
        cands.append({"term": s, "n": len(s.split()), "count": 0, "npmi": 0.0, "seeker": 0, "church": 0,
                      "doc_freq": 0, "by_tradition": {}, "seed": True, "n_sources": 0, "tradition_sources": {},
                      "top_source_share": 0.0, "tradition_top_share": {}})

    d = ctx.stage_dir(NAME)
    write_jsonl(d / "candidates.jsonl", sorted(cands, key=lambda x: -x["count"]))
    totals = {"tokens": dict(tok_totals), "docs": dict(n_docs), "order_totals": dict(order_totals)}
    write_json(d / "totals.json", totals)
    summary = {"candidates": len(cands), "seeds_absent": len(seed_toks - present), "sources": len(src_id),
               f"dropped_under_{min_sources}_sources": dropped_sources, **totals["docs"]}
    log.info("counts: %s", summary)
    return summary
