"""08 stance: is a term contested or judgment-laden?

- negation_rate (deterministic, from stage 06): how often it appears after not/no/never...
- LLM labels a sample of contexts as approving / critical / neutral / mixed (one call per term)
  contestedness   = 2 * min(p_approving, p_critical)     (1.0 = evenly split)
  tradition_split = max - min approval rate across groups with >= 3 labeled snippets
"""
from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict

from .. import prompts
from ..util import Ctx, read_jsonl, write_jsonl

log = logging.getLogger("lexicon.stance")
NAME = "08_stance"


def _fake_for(contexts):
    def fake(_messages):
        labels = []
        for i, c in enumerate(contexts):
            if c["negated"]:
                s = "critical"
            else:
                h = int(hashlib.md5(c["text"].encode()).hexdigest(), 16) % 3
                s = ["approving", "neutral", "approving"][h]
            labels.append({"i": i + 1, "stance": s})
        return {"labels": labels}
    return fake


def label(ctx: Ctx, term: str, contexts: list[dict]) -> list[str | None]:
    numbered = "\n".join(f"{i + 1}. {c['text']}" for i, c in enumerate(contexts))
    msgs = [{"role": "system", "content": prompts.STANCE_SYSTEM},
            {"role": "user", "content": f'TERM: "{term}"\n\nSNIPPETS:\n{numbered}'}]

    def check(v):
        got = sorted(x["i"] for x in v["labels"])
        want = list(range(1, len(contexts) + 1))
        return None if got == want else f"need exactly one label for each i in 1..{len(contexts)}, got {got}"

    v = ctx.llm.complete_json("stance", msgs, prompts.STANCE_SCHEMA, model=ctx.cfg["llm"]["small_chat_model"],
                              fake=_fake_for(contexts), check=check)
    if not v:
        return [None] * len(contexts)
    by_i = {x["i"]: x["stance"] for x in v["labels"]}
    return [by_i.get(i + 1) for i in range(len(contexts))]


def run(ctx: Ctx) -> dict:
    st = ctx.cfg["stance"]
    occ = {r["term"]: r for r in read_jsonl(ctx.work / "06_contexts" / "occurrences.jsonl")}
    out, called = [], 0
    for row in read_jsonl(ctx.work / "06_contexts" / "contexts.jsonl"):
        t = row["term"]
        cs = row["contexts"][: st["sample_per_term"]]
        rec = {"term": t, "negation_rate": occ.get(t, {}).get("negation_rate", 0.0),
               "contestedness": None, "tradition_split": None, "stance_counts": {}}
        if st["use_llm"] and len(cs) >= 4:
            labels = label(ctx, t, cs)
            called += 1
            cnt = Counter(x for x in labels if x)
            n = sum(cnt.values())
            if n:
                p_app = (cnt["approving"] + 0.5 * cnt["mixed"]) / n
                p_cri = (cnt["critical"] + 0.5 * cnt["mixed"]) / n
                rec["contestedness"] = round(2 * min(p_app, p_cri), 4)
                per_group = defaultdict(list)
                for c, lab in zip(cs, labels):
                    if lab:
                        per_group[c["group"]].append(lab in ("approving",))
                rates = [sum(v) / len(v) for v in per_group.values() if len(v) >= 3]
                rec["tradition_split"] = round(max(rates) - min(rates), 4) if len(rates) >= 2 else 0.0
                rec["stance_counts"] = dict(cnt)
        out.append(rec)
    write_jsonl(ctx.stage_dir(NAME) / "stance.jsonl", out)
    summary = {"terms": len(out), "llm_calls": called}
    log.info("stance: %s", summary)
    return summary
