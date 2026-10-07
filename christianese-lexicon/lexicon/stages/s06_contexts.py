"""06 contexts: one pass over the corpus for every shortlisted term.

Produces
- occurrences.jsonl: authoritative counts (total, seeker, church, by tradition, negated)
  plus co-occurrence counts with the observable vocabulary (for grounding, stage 09)
- contexts.jsonl: a balanced sample of short, scrubbed keyword-in-context snippets,
  stratified across traditions and sides (for polysemy, stance, drafting)
"""
from __future__ import annotations

import logging
import random
from collections import Counter, defaultdict

from ..text import find_term, negated_at, tokenize
from ..util import Ctx, read_jsonl, read_list, write_json, write_jsonl
from .s01_ingest import load_docs

log = logging.getLogger("lexicon.contexts")
NAME = "06_contexts"


def group_of(doc: dict) -> str:
    if doc["side"] == "seeker":
        return "seeker"
    return f"church:{doc['tradition'] or 'unlabeled'}"


def run(ctx: Ctx) -> dict:
    per_term = ctx.cfg["contexts"]["per_term"]
    w = ctx.cfg["privacy"]["context_window_tokens"]
    gwin = ctx.cfg["grounding"]["window"]
    terms = [r["term"] for r in read_jsonl(ctx.work / "05_shortlist" / "shortlist.jsonl")]
    term_toks = {t: tokenize(t) for t in terms}
    by_first = defaultdict(list)
    for t, tt in term_toks.items():
        if tt:
            by_first[tt[0]].append(t)
    observables = read_list(ctx.path("observables"))
    obs_single = {o for o in observables if " " not in o}

    rng = random.Random(13)
    occ = {t: {"count": 0, "seeker": 0, "church": 0, "negated": 0, "by_tradition": Counter(),
               "docs": 0, "cooc": Counter()} for t in terms}
    reservoirs: dict[str, dict[str, list]] = {t: defaultdict(list) for t in terms}
    seen_per_group: dict[str, Counter] = {t: Counter() for t in terms}
    obs_total = Counter()
    n_tokens = 0
    cap = per_term  # per group reservoir size

    for doc in load_docs(ctx):
        toks = tokenize(doc["text"])
        n_tokens += len(toks)
        for tok in toks:
            if tok in obs_single:
                obs_total[tok] += 1
        g = group_of(doc)
        hit_terms = set()
        for i, tok in enumerate(toks):
            for t in by_first.get(tok, ()):
                tt = term_toks[t]
                if toks[i:i + len(tt)] != tt:
                    continue
                o = occ[t]
                o["count"] += 1
                o[doc["side"]] += 1
                if doc["tradition"] and doc["side"] == "church":
                    o["by_tradition"][doc["tradition"]] += 1
                if negated_at(toks, i):
                    o["negated"] += 1
                hit_terms.add(t)
                lo, hi = max(0, i - gwin), min(len(toks), i + len(tt) + gwin)
                window = set(toks[lo:i]) | set(toks[i + len(tt):hi])
                for ob in window & obs_single:
                    o["cooc"][ob] += 1
                # reservoir sample per (term, group)
                snippet = " ".join(toks[max(0, i - w): i + len(tt) + w])
                seen_per_group[t][g] += 1
                res = reservoirs[t][g]
                k = seen_per_group[t][g]
                item = {"text": snippet, "group": g, "tradition": doc["tradition"], "side": doc["side"],
                        "negated": negated_at(toks, i)}
                if len(res) < cap:
                    res.append(item)
                else:
                    j = rng.randrange(k)
                    if j < cap:
                        res[j] = item
        for t in hit_terms:
            occ[t]["docs"] += 1

    ctx_rows, occ_rows = [], []
    for t in terms:
        groups = {g: list(v) for g, v in reservoirs[t].items()}
        for v in groups.values():
            rng.shuffle(v)
        picked = []
        while len(picked) < per_term and any(groups.values()):  # round-robin = balanced
            for g in sorted(groups):
                if groups[g] and len(picked) < per_term:
                    picked.append(groups[g].pop())
        ctx_rows.append({"term": t, "contexts": picked})
        o = occ[t]
        occ_rows.append({"term": t, "count": o["count"], "seeker": o["seeker"], "church": o["church"],
                         "docs": o["docs"], "negated": o["negated"],
                         "negation_rate": round(o["negated"] / o["count"], 4) if o["count"] else 0.0,
                         "by_tradition": dict(o["by_tradition"]), "cooc": dict(o["cooc"])})
    d = ctx.stage_dir(NAME)
    write_jsonl(d / "contexts.jsonl", ctx_rows)
    write_jsonl(d / "occurrences.jsonl", occ_rows)
    write_json(d / "observable_totals.json", {"tokens": n_tokens, "counts": dict(obs_total)})
    summary = {"terms": len(terms), "terms_with_contexts": sum(1 for r in ctx_rows if r["contexts"]),
               "tokens_scanned": n_tokens}
    log.info("contexts: %s", summary)
    return summary
