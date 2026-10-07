"""09 grounding: which concrete, observable words travel with each term?

lift(term, obs) = P(obs in window | term) / P(obs in a random window of the same size)
Reported as log2 lift for observables co-occurring >= min_cooccurrence times.
This turns "contemporary means band + drums + coffee" into a measured claim.
"""
from __future__ import annotations

import logging
import math

from ..util import Ctx, read_json, read_jsonl, write_jsonl

log = logging.getLogger("lexicon.grounding")
NAME = "09_grounding"


def run(ctx: Ctx) -> dict:
    g = ctx.cfg["grounding"]
    win, min_co, top_k = g["window"], g["min_cooccurrence"], g["top_k"]
    tot = read_json(ctx.work / "06_contexts" / "observable_totals.json")
    n_tok = max(tot["tokens"], 1)
    obs_ct = tot["counts"]
    out, grounded = [], 0
    for r in read_jsonl(ctx.work / "06_contexts" / "occurrences.jsonl"):
        c_t = r["count"]
        items = []
        for ob, co in r["cooc"].items():
            if co < min_co or c_t == 0 or obs_ct.get(ob, 0) == 0:
                continue
            p_cond = co / c_t
            p_base = min(1.0, 2 * win * obs_ct[ob] / n_tok)
            lift = p_cond / p_base if p_base else 0
            if lift > 1:
                items.append({"observable": ob, "cooc": co, "log2_lift": round(math.log2(lift), 3)})
        items.sort(key=lambda x: (-x["log2_lift"], -x["cooc"]))
        if items:
            grounded += 1
        out.append({"term": r["term"], "observables": items[:top_k]})
    write_jsonl(ctx.stage_dir(NAME) / "grounding.jsonl", out)
    summary = {"terms": len(out), "grounded": grounded}
    log.info("grounding: %s", summary)
    return summary
