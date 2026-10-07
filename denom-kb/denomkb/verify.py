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
    return not v or v.get("model") != model or v.get("version") != prompts.VERIFY_VERSION


VERDICTS = {"supported", "partial", "unsupported"}


def normalize(v):
    """Coerce near-miss shapes from models that ignore the JSON schema (seen with gpt-oss on Ollama cloud):
    {"verdicts": [{"id": 1, "verdict": "supported"}]}            (missing reason)
    {"1": {"verdict": "supported", "reason": "..."}, "2": ...}    (keyed by id)
    {"1": "supported", ...} / {"verdicts": {"1": ...}}"""
    if isinstance(v, list):
        v = {"verdicts": v}
    if not isinstance(v, dict):
        return v
    items = v.get("verdicts", v)
    if isinstance(items, dict):
        items = [({"id": k, **x} if isinstance(x, dict) else {"id": k, "verdict": x}) for k, x in items.items()]
    out = []
    for x in items if isinstance(items, list) else []:
        if not isinstance(x, dict):
            continue
        try:
            i = int(str(x.get("id", "")).strip("[] "))
        except ValueError:
            continue
        verdict = str(x.get("verdict", "")).strip().lower()
        if verdict not in VERDICTS:
            continue
        out.append({"id": i, "verdict": verdict, "reason": str(x.get("reason", ""))[:300]})
    return {"verdicts": out}


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
                                      partial=True, think=think, normalize=normalize)
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


# Ethically sensitive fields are never applied automatically, even when the verifier passes them:
# a person must accept them (review_decision column in proposals.csv -> `denomkb apply-review`).
SENSITIVE_FIELDS = {
    "practice_culture.lgbtq_relationships", "practice_culture.same_sex_marriage", "practice_culture.abortion",
    "governance.women_ordination", "governance.women_senior_pastors", "governance.women_preaching",
    "practice_culture.divorce_remarriage",
}


def applied(p: dict) -> bool:
    """Should this proposal change the knowledge base? Unverified runs behave as before."""
    if p.get("human_decision") == "accept":
        return True
    if p.get("human_decision") == "reject":
        return False
    if p["field_id"] in SENSITIVE_FIELDS and p["action"] in ("fill", "conflict"):
        return False
    if p["action"] not in CHECKED_ACTIONS or p["source_type"] == "wikidata":
        return True
    v = p.get("verify")
    return v is None or v["verdict"] == "supported"


def apply_review(work, csv_path) -> dict:
    """Copy accept/reject decisions from a reviewed proposals.csv back onto the per-group proposals."""
    import csv
    from pathlib import Path
    decisions = {}
    with open(csv_path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            d = (r.get("review_decision") or "").strip().lower()
            if d in ("accept", "reject"):
                decisions[(r["group_id"], r["field_id"], r["action"], r["new_value"])] = (d, r.get("review_notes", ""))
    n = 0
    for path in Path(work, "groups").glob("*/proposals.json"):
        props = read_json(path)
        changed = False
        for p in props:
            k = (p["group_id"], p["field_id"], p["action"], p["new_value"])
            if k in decisions:
                p["human_decision"], p["human_notes"] = decisions[k]
                changed = True
                n += 1
        if changed:
            write_json(path, props)
    return {"decisions_applied": n}
