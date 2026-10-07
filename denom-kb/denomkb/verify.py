"""Second-pass verifier: does each quote actually support its proposed value?

The extraction step guarantees a quote EXISTS in the source (verbatim check). A 40-claim
hand audit of the first full run showed that is not enough: only 35% of fills were fully
supported by their quote. This pass asks a separate (ideally larger) model a narrow yes/no
question per claim and records the verdict on the proposal. Export applies only
"supported" claims; "partial" and "unsupported" stay visible in proposals.csv for review.

Runs largest groups first, batches claims, caches every call, and stops cleanly if the
model server rate-limits, so it can be resumed by running it again.
"""
from __future__ import annotations

import logging
import time

from . import prompts
from .llm import LLMError
from .util import read_json, write_json

log = logging.getLogger("denomkb.verify")
CHECKED_ACTIONS = {"fill", "confirm", "conflict"}


def needs_check(p: dict, model: str) -> bool:
    if p["action"] not in CHECKED_ACTIONS or p["source_type"] == "wikidata":
        return False
    v = p.get("verify")
    return not v or v.get("model") != model


def _claims_block(g_name: str, kb_fields: dict, batch: list[dict]) -> str:
    lines = [f"GROUP: {g_name}", ""]
    for i, p in enumerate(batch, 1):
        desc = kb_fields.get(p["field_id"], {}).get("description", p["field_id"])
        lines.append(f"[{i}] FIELD: {desc} ({p['field_id']})\n    VALUE: {p['new_value']}\n    QUOTE: \"{p['quote']}\"")
    return "\n".join(lines)


def run(cfg: dict, root, kb, llm, groups: list[dict], dry_run: bool = False) -> dict:
    vc = cfg.get("verify", {})
    model = vc.get("model", cfg["llm"]["chat_model"])
    per_call = vc.get("claims_per_call", 8)
    thinking = model.startswith(("gpt-oss", "qwen3", "deepseek-r1"))
    think = vc.get("think", "low" if model.startswith("gpt-oss") else None) if thinking else None
    work = (root / cfg["work_dir"]).resolve()
    stats = {"checked": 0, "supported": 0, "partial": 0, "unsupported": 0, "groups": 0, "stopped_early": False}
    t0 = time.time()
    for g in groups:
        path = work / "groups" / g["id"] / "proposals.json"
        if not path.exists():
            continue
        props = read_json(path)
        todo = [p for p in props if needs_check(p, model)]
        if not todo:
            continue
        log.info("verify %s: %d claims", g["census_name"], len(todo))
        for i in range(0, len(todo), per_call):
            batch = todo[i:i + per_call]
            msgs = [{"role": "system", "content": prompts.VERIFY_SYSTEM},
                    {"role": "user", "content": _claims_block(g["census_name"], kb.fields, batch)}]

            def check(v, n=len(batch)):
                got = sorted(x["id"] for x in v["verdicts"])
                return None if got == list(range(1, n + 1)) else f"need exactly one verdict for each id 1..{n}, got {got}"

            def fake(_m, b=batch):
                from .index import norm_for_match
                out = []
                for j, p in enumerate(b, 1):
                    words = [w for w in norm_for_match(p["new_value"]).split() if len(w) > 3]
                    hit = sum(w in norm_for_match(p["quote"]) for w in words)
                    out.append({"id": j, "verdict": "supported" if words and hit >= max(1, len(words) // 2) else "unsupported",
                                "reason": "dry-run word overlap"})
                return {"verdicts": out}
            try:
                v = llm.complete_json("verify", msgs, prompts.VERIFY_SCHEMA, model=model, fake=fake, check=check,
                                      partial=True, think=think)
            except LLMError as e:
                log.warning("verifier unavailable (%s); stopping. Re-run `verify` later to resume.", e)
                stats["stopped_early"] = True
                write_json(path, props)
                return {**stats, "minutes": round((time.time() - t0) / 60, 1)}
            by_id = {x["id"]: x for x in (v or {}).get("verdicts", [])}
            for j, p in enumerate(batch, 1):
                x = by_id.get(j)
                if x:
                    p["verify"] = {"verdict": x["verdict"], "reason": x["reason"], "model": model,
                                   "version": prompts.VERIFY_VERSION}
                    stats["checked"] += 1
                    stats[x["verdict"]] += 1
            write_json(path, props)          # save after every batch: resumable
        stats["groups"] += 1
    return {**stats, "minutes": round((time.time() - t0) / 60, 1)}


def applied(p: dict) -> bool:
    """Should this proposal change the knowledge base? Unverified runs behave as before."""
    if p["action"] not in CHECKED_ACTIONS or p["source_type"] == "wikidata":
        return True
    v = p.get("verify")
    return v is None or v["verdict"] == "supported"
