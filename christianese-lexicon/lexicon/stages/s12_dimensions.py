"""12 dimensions: do the drafted terms cluster into the dimensions we assumed?

Embeds "term: glosses" for every drafted entry, clusters them, has the model name
each cluster, and cross-tabulates clusters against the drafted `category`.
A cluster that matches no category is a candidate missing dimension.
"""
from __future__ import annotations

import logging
from collections import Counter

from sklearn.cluster import KMeans

from .. import prompts
from ..util import Ctx, read_json, write_json

log = logging.getLogger("lexicon.dimensions")
NAME = "12_dimensions"


def run(ctx: Ctx) -> dict:
    entries = read_json(ctx.work / "11_draft" / "lexicon_draft.json")
    k = min(ctx.cfg["dimensions"]["n_clusters"], max(1, len(entries) // 3))
    if len(entries) < 4 or k < 2:
        write_json(ctx.stage_dir(NAME) / "dimensions.json", {"clusters": [], "note": "too few entries"})
        return {"clusters": 0}
    texts = [f"{e['term']}: " + "; ".join(s["gloss"] for s in e["senses"]) for e in entries]
    X = ctx.llm.embed(texts)
    lab = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(X)
    clusters = []
    for c in range(k):
        members = [entries[i] for i in range(len(entries)) if lab[i] == c]
        listing = "\n".join(f"- {m['term']}: {m['senses'][0]['gloss']}" for m in members[:30])
        msgs = [{"role": "system", "content": prompts.DIMENSION_SYSTEM}, {"role": "user", "content": listing}]
        named = ctx.llm.complete_json(
            "dimension_name", msgs, prompts.DIMENSION_SCHEMA,
            fake=lambda _m, c=c: {"name": f"cluster {c}", "description": "dry-run"}) or {}
        cats = Counter(m["category"] for m in members)
        clusters.append({"id": c, "name": named.get("name"), "description": named.get("description"),
                         "size": len(members), "categories": dict(cats),
                         "purity": round(cats.most_common(1)[0][1] / len(members), 3),
                         "terms": [m["term"] for m in members]})
    write_json(ctx.stage_dir(NAME) / "dimensions.json", {"clusters": clusters})
    summary = {"clusters": k, "mean_purity": round(sum(c["purity"] for c in clusters) / k, 3)}
    log.info("dimensions: %s", summary)
    return summary
