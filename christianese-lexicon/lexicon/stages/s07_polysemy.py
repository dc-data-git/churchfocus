"""07 polysemy: does one term carry several meanings?

Per term:
- embed its contexts, k-means for k in k_range, keep best silhouette
  -> polysemy = max(0, best silhouette)       (clear sense clusters)
- tradition drift = max cosine distance between per-tradition mean embeddings
  -> high when the same word is used differently by different traditions
- nmi = normalized mutual information between sense clusters and tradition labels
"""
from __future__ import annotations

import logging

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import normalized_mutual_info_score, silhouette_score

from ..util import Ctx, read_jsonl, write_jsonl

log = logging.getLogger("lexicon.polysemy")
NAME = "07_polysemy"


def analyze(X: np.ndarray, labels: list[str | None], k_range: tuple[int, int]) -> dict:
    best = (-1.0, None, None)
    for k in range(k_range[0], k_range[1] + 1):
        if len(X) <= k + 1:
            break
        km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(X)
        if len(set(km.labels_)) < 2:
            continue
        s = silhouette_score(X, km.labels_, metric="cosine")
        if s > best[0]:
            best = (s, k, km.labels_)
    sil, k, lab = best
    groups: dict[str, list[int]] = {}
    for i, g in enumerate(labels):
        if g:
            groups.setdefault(g, []).append(i)
    means = {g: X[idx].mean(axis=0) for g, idx in groups.items() if len(idx) >= 5}
    drift = 0.0
    keys = list(means)
    for a in range(len(keys)):
        for b in range(a + 1, len(keys)):
            u, v = means[keys[a]], means[keys[b]]
            cos = float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9))
            drift = max(drift, 1 - cos)
    nmi = 0.0
    if lab is not None:
        idx = [i for i, g in enumerate(labels) if g]
        if len(set(labels[i] for i in idx)) >= 2 and len(idx) >= 6:
            nmi = float(normalized_mutual_info_score([labels[i] for i in idx], [lab[i] for i in idx]))
    return {"polysemy": round(max(0.0, sil), 4), "best_k": k, "drift": round(drift, 4), "nmi": round(nmi, 4),
            "clusters": [int(x) for x in lab] if lab is not None else None}


def run(ctx: Ctx) -> dict:
    p = ctx.cfg["polysemy"]
    k_range, min_ctx = tuple(p["k_range"]), p["min_contexts"]
    out, analyzed = [], 0
    for row in read_jsonl(ctx.work / "06_contexts" / "contexts.jsonl"):
        cs = row["contexts"]
        if len(cs) < min_ctx:
            out.append({"term": row["term"], "polysemy": None, "drift": None, "nmi": None, "n": len(cs)})
            continue
        X = ctx.llm.embed([c["text"] for c in cs])
        labels = [c["group"] if c["side"] == "church" and c["tradition"] else None for c in cs]
        r = analyze(X, labels, k_range)
        out.append({"term": row["term"], "n": len(cs), **r})
        analyzed += 1
    write_jsonl(ctx.stage_dir(NAME) / "polysemy.jsonl", out)
    summary = {"analyzed": analyzed, "skipped_thin": len(out) - analyzed}
    log.info("polysemy: %s", summary)
    return summary
