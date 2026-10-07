"""13 coverage: how much of what NEW seekers ask for does the lexicon recognize?

Runs the stage-03 extractor on the held-out seeker docs (never used to build the
lexicon). A requested phrase is covered if, token-wise, it contains or is contained
in a lexicon term or alias. Reports overall coverage, coverage by category, and the
most frequent uncovered phrases (the to-do list for the next lexicon version).
"""
from __future__ import annotations

import logging
from collections import Counter

from ..text import tokenize
from ..util import Ctx, read_json, write_json
from .s01_ingest import load_holdout
from .s03_seeker import mine

log = logging.getLogger("lexicon.coverage")
NAME = "13_coverage"


def _contains(a: list[str], b: list[str]) -> bool:
    n = len(b)
    return n > 0 and any(a[i:i + n] == b for i in range(len(a) - n + 1))


def covered(phrase: str, lex_toks: list[list[str]]) -> bool:
    p = tokenize(phrase)
    return any(_contains(p, t) or _contains(t, p) for t in lex_toks)


def run(ctx: Ctx, lexicon_path=None) -> dict:
    lex = read_json(lexicon_path or (ctx.work / "11_draft" / "lexicon_draft.json"))
    if isinstance(lex, dict):
        lex = lex["entries"]
    lex_toks = []
    for e in lex:
        for s in [e["term"], *e.get("aliases", [])]:
            t = tokenize(s)
            if t:
                lex_toks.append(t)
    rows, _ = mine(ctx, load_holdout(ctx), ctx.cfg["seeker"]["max_requests"])
    if not rows:
        res = {"holdout_attributes": 0, "coverage": None, "note": "no held-out seeker requests found"}
        write_json(ctx.stage_dir(NAME) / "coverage.json", res)
        return res
    hits, by_cat, unc = 0, Counter(), Counter()
    cat_tot = Counter()
    for r in rows:
        cat_tot[r["category"]] += 1
        if covered(r["phrase"], lex_toks):
            hits += 1
            by_cat[r["category"]] += 1
        else:
            unc[r["phrase"]] += 1
    res = {
        "holdout_attributes": len(rows),
        "coverage": round(hits / len(rows), 4),
        "by_category": {c: round(by_cat[c] / n, 4) for c, n in cat_tot.items()},
        "top_uncovered": unc.most_common(50),
    }
    write_json(ctx.stage_dir(NAME) / "coverage.json", res)
    log.info("coverage: %.1f%% of %d held-out requested attributes", 100 * res["coverage"], len(rows))
    return {k: v for k, v in res.items() if k != "top_uncovered"}
