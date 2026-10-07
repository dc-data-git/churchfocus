"""11 draft: the local model drafts a lexicon entry for each top-ranked term.

Self-correction: schema errors AND semantic errors (unknown feature ids, missing
senses for a polysemous term, missing neutral options for a contested term) are
sent back to the model for repair. Every entry is marked vetted=false until a
human reviews it (see `lexicon review`).
"""
from __future__ import annotations

import datetime as dt
import logging

import yaml

from .. import prompts
from ..util import Ctx, read_json, read_jsonl, write_json, write_jsonl

log = logging.getLogger("lexicon.draft")
NAME = "11_draft"


def load_features(ctx: Ctx) -> dict:
    with open(ctx.path("features"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def feature_block(features: dict) -> str:
    return "\n".join(f"- {fid}: {v['desc']} | values: {', '.join(map(str, v['values']))}"
                     for fid, v in features.items())


def evidence_block(r: dict, contexts: list[dict], n_ctx: int) -> str:
    obs = ", ".join(f"{o['observable']} (x{o['cooc']})" for o in r["observables"]) or "none measured"
    trad = ", ".join(f"{t}:{c}" for t, c in sorted(r["by_tradition"].items(), key=lambda kv: -kv[1])) or "none"
    lines = [
        f'TERM: "{r["term"]}"',
        f"Corpus count: {r['count']} (seeker-side {r['seeker_count']}, church-side {r['church_count']})",
        f"Seekers asked for it {r['seeker_want']}x and asked to avoid it {r['seeker_avoid']}x",
        f"Church-side use by tradition: {trad}",
        f"Most distinctive of tradition: {r['top_tradition']} (z={r['top_tradition_z']})",
        f"Polysemy score: {r['polysemy']}  tradition drift: {r['drift']}  contestedness: {r['contestedness']}  "
        f"negation rate: {r['negation_rate']}",
        f"Co-occurring observable words: {obs}",
        "",
        "SAMPLE CONTEXTS (scrubbed; group in brackets):",
    ]
    for c in contexts[:n_ctx]:
        lines.append(f"[{c['group']}] ...{c['text']}...")
    if not contexts:
        lines.append("(none in corpus: rely on general knowledge and say so in notes)")
    return "\n".join(lines)


def make_check(features: dict):
    known = set(features)

    def check(v: dict) -> str | None:
        if v["relevance"] == "not_relevant":
            return None
        errs = []
        for s in v["senses"]:
            for f in s["features"]:
                fid = f["feature"]
                if fid not in known and not fid.startswith("proposed."):
                    errs.append(f"unknown feature id '{fid}' (use the vocabulary or 'proposed.<id>')")
        max_f = 6
        for s_ in v["senses"]:
            if len(s_["features"]) > max_f:
                errs.append(f"sense '{s_['gloss'][:40]}' lists {len(s_['features'])} features; list only the "
                            f"features this term actually implies (at most {max_f})")
        types = set(v["term_types"])
        if "polysemous" in types and len(v["senses"]) < 2:
            errs.append("term_types includes polysemous but only one sense was given")
        if "contested" in types and not (2 <= len(v["neutral_options"]) <= 4):
            errs.append("contested terms need 2-4 neutral_options")
        if types & {"polysemous", "contested", "evaluative"} and not v["disambiguation_question"].strip():
            errs.append("a disambiguation_question is required for polysemous/contested/evaluative terms")
        return "; ".join(errs[:6]) or None
    return check


def _fake_for(r: dict, features: dict):
    def fake(_m):
        poly = (r["polysemy"] or 0) > 0.15 or (r["drift"] or 0) > 0.2
        contested = (r["contestedness"] or 0) > 0.4
        types = ["descriptive"]
        if poly:
            types = ["polysemous"]
        if contested:
            types.append("contested")
        if r["negation_rate"] > 0.3:
            types.append("negatable")
        fid = next(iter(features))
        sense = {"gloss": f"(dry-run) meaning of {r['term']}", "traditions": ["all"],
                 "features": [{"feature": fid, "value": "present"}]}
        rel = "not_relevant" if (r["polysemy"] or 0) < 0.2 and r["count"] > 0 else "church_vocabulary"
        return {"relevance": rel, "aliases": [], "category": r.get("seeker_category") or "culture", "term_types": types,
                "senses": [sense, dict(sense, traditions=[r["top_tradition"] or "all"])] if poly else [sense],
                "disambiguation_question": f"(dry-run) What do you mean by '{r['term']}'?" if poly or contested else "",
                "neutral_options": ["(dry-run) position A", "(dry-run) position B"] if contested else [],
                "notes": "dry-run placeholder"}
    return fake


def run(ctx: Ctx) -> dict:
    top_n = ctx.cfg["draft"]["top_n"]
    n_ctx = ctx.cfg["draft"]["contexts_in_prompt"]
    features = load_features(ctx)
    schema = read_json(ctx.path("schema"))
    ctxs = {r["term"]: r["contexts"] for r in read_jsonl(ctx.work / "06_contexts" / "contexts.jsonl")}
    all_ranked = list(read_jsonl(ctx.work / "10_rank" / "ranked.jsonl"))
    ranked = all_ranked[:top_n]
    # seed terms are the vocabulary the team already knows matters: draft every one the corpus
    # actually uses, even when it ranks below top_n
    min_seed = ctx.cfg["draft"].get("seed_min_count", 3)
    have = {r["term"] for r in ranked}
    ranked += [r for r in all_ranked[top_n:] if r.get("seed") and r["count"] >= min_seed and r["term"] not in have]
    system = prompts.DRAFT_SYSTEM + "\n\nFEATURE VOCABULARY:\n" + feature_block(features)
    check = make_check(features)
    model = ctx.cfg["llm"]["chat_model"]

    entries, failures, rejected = [], [], []
    for r in ranked:
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": evidence_block(r, ctxs.get(r["term"], []), n_ctx)}]
        v = ctx.llm.complete_json("draft", msgs, schema, model=model, fake=_fake_for(r, features), check=check)
        if v is None:
            failures.append({"term": r["term"], "rank": r["rank"]})
            continue
        if v["relevance"] == "not_relevant":
            rejected.append({"term": r["term"], "rank": r["rank"], "notes": v.get("notes", "")})
            continue
        entries.append({
            "term": r["term"], **v,
            "evidence_stats": {k: r[k] for k in ("rank", "score", "count", "seeker_count", "church_count",
                                                 "seeker_requests", "seeker_want", "seeker_avoid", "z_english",
                                                 "top_tradition", "polysemy", "drift", "contestedness",
                                                 "tradition_split", "negation_rate", "observables")},
            "provenance": {"drafted_by": "dry-run" if ctx.dry_run else model, "prompt_version": prompts.DRAFT_VERSION,
                           "drafted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                           "vetted": False, "reviewers": [],
                           "low_evidence": r["count"] < 5},
        })
    d = ctx.stage_dir(NAME)
    write_json(d / "lexicon_draft.json", entries)
    write_jsonl(d / "failures.jsonl", failures)
    write_jsonl(d / "rejected.jsonl", rejected)
    proposed = sorted({f["feature"] for e in entries for s in e["senses"] for f in s["features"]
                       if f["feature"].startswith("proposed.")})
    write_json(d / "proposed_features.json", proposed)
    summary = {"drafted": len(entries), "rejected_not_relevant": len(rejected), "failed": len(failures), "proposed_features": len(proposed)}
    log.info("draft: %s", summary)
    return summary
