"""04 keyness: (a) how distinctively churchy a term is vs. general English,
(b) which traditions a term marks (shibboleths).

Both use the log-odds ratio with an informative Dirichlet prior
(Monroe, Colaresi & Quinn 2008, "Fightin' Words"), reported as a z-score.
General-English reference frequencies come from the `wordfreq` package; for
multi-word terms wordfreq's estimate is an over-estimate, which makes phrase
keyness conservative.
"""
from __future__ import annotations

import logging
import math

from wordfreq import word_frequency

from ..util import Ctx, read_json, read_jsonl, read_list, write_jsonl

log = logging.getLogger("lexicon.keyness")
NAME = "04_keyness"


def log_odds_z(y_i: float, n_i: float, y_j: float, n_j: float, a_w: float, a0: float) -> float:
    a_w = max(a_w, 1e-6)
    li = math.log((y_i + a_w) / max(n_i + a0 - y_i - a_w, 1e-9))
    lj = math.log((y_j + a_w) / max(n_j + a0 - y_j - a_w, 1e-9))
    var = 1.0 / (y_i + a_w) + 1.0 / (y_j + a_w)
    return (li - lj) / math.sqrt(var)


def run(ctx: Ctx) -> dict:
    k = ctx.cfg["keyness"]
    n_ref, a0, min_docs = k["reference_corpus_size"], k["prior_strength"], k["min_tradition_docs"]
    totals = read_json(ctx.work / "02_counts" / "totals.json")
    n_corpus = max(totals["tokens"].get("all", 1), 1)
    trads = [t[2:] for t, nd in totals["docs"].items() if t.startswith("t:") and nd >= min_docs]
    church_tokens = totals["tokens"].get("church", 1)
    max_ref = ctx.cfg["shortlist"].get("max_ref_freq", 1.5e-4)
    excluded = set(read_list(ctx.path("generic_terms"))) if "generic_terms" in ctx.cfg else set()
    if ctx.cfg.get("filler_terms"):
        excluded |= set(read_list(ctx.path("filler_terms")))

    out = []
    for c in read_jsonl(ctx.work / "02_counts" / "candidates.jsonl"):
        y = c["count"]
        ref_f = max(word_frequency(c["term"], "en"), 1e-9)
        y_ref = ref_f * n_ref
        a_w = a0 * (y + y_ref) / (n_corpus + n_ref)
        z_eng = log_odds_z(y, n_corpus, y_ref, n_ref, a_w, a0) if y > 0 else 0.0

        tz = {}
        for t in trads:
            yi = c["by_tradition"].get(t, 0)
            ni = totals["tokens"].get(f"t:{t}", 1)
            yj = c["church"] - yi
            nj = max(church_tokens - ni, 1)
            aw = a0 * max(c["church"], 0.5) / max(church_tokens, 1)
            tz[t] = round(log_odds_z(yi, ni, yj, nj, aw, a0), 3)
        # A tradition marker must be shared vocabulary of that tradition: used by >= min_ts of its
        # sources, with no single source supplying more than max_share of the tradition's uses,
        # and not a filler / generic / common-English word.
        min_ts = k.get("min_tradition_sources", 0)
        max_share = k.get("max_source_share", 1.0)
        ts, tsh = c.get("tradition_sources", {}), c.get("tradition_top_share", {})
        if not c["seed"] and (c["term"] in excluded or ref_f > max_ref):
            markable = {}
        else:
            markable = {t: z for t, z in tz.items()
                        if (not min_ts or ts.get(t, 0) >= min_ts) and tsh.get(t, 0) <= max_share}
        top = max(markable.items(), key=lambda kv: kv[1]) if markable else (None, 0.0)
        out.append({"term": c["term"], "z_english": round(z_eng, 3), "ref_freq": ref_f,
                    "tradition_z": tz, "top_tradition": top[0], "top_tradition_z": top[1]})
    write_jsonl(ctx.stage_dir(NAME) / "keyness.jsonl", out)
    summary = {"terms": len(out), "traditions_compared": trads}
    log.info("keyness: %s", summary)
    return summary
