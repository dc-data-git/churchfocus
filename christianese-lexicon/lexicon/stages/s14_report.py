"""14 report: human-readable report + the exported lexicon.json for the MCP server."""
from __future__ import annotations

import datetime as dt
import json
import logging
import statistics
from collections import defaultdict
from pathlib import Path

from .. import __version__, prompts
from ..util import Ctx, read_json, read_jsonl, write_json

log = logging.getLogger("lexicon.report")
NAME = "14_report"


def _load(p: Path, default):
    return read_json(p) if p.exists() else default


def call_stats(work: Path) -> list[dict]:
    p = work / "calls.jsonl"
    if not p.exists():
        return []
    agg = defaultdict(lambda: {"calls": 0, "ok": 0, "ms": [], "repairs": 0, "prompt_tokens": 0, "completion_tokens": 0})
    for r in read_jsonl(p):
        a = agg[(r["task"], r["model"])]
        a["calls"] += 1
        a["ok"] += int(r.get("ok", False))
        a["ms"].append(r.get("ms", 0))
        a["repairs"] += r.get("repairs", 0)
        a["prompt_tokens"] += r.get("prompt_tokens", 0)
        a["completion_tokens"] += r.get("completion_tokens", 0)
    out = []
    for (task, model), a in sorted(agg.items()):
        out.append({"task": task, "model": model, "calls": a["calls"], "ok_rate": round(a["ok"] / a["calls"], 3),
                    "median_ms": int(statistics.median(a["ms"])) if a["ms"] else 0,
                    "total_min": round(sum(a["ms"]) / 60000, 1), "repairs": a["repairs"],
                    "prompt_tokens": a["prompt_tokens"], "completion_tokens": a["completion_tokens"]})
    return out


def _fmt(v):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def run(ctx: Ctx) -> dict:
    W, n = ctx.work, ctx.cfg["report"]["top_n_tables"]
    ingest = _load(W / "01_ingest" / "stats.json", {})
    ranked = list(read_jsonl(W / "10_rank" / "ranked.jsonl")) if (W / "10_rank" / "ranked.jsonl").exists() else []
    lex = _load(W / "11_draft" / "lexicon_draft.json", [])
    dims = _load(W / "12_dimensions" / "dimensions.json", {"clusters": []})
    cov = _load(W / "13_coverage" / "coverage.json", {})
    calls = call_stats(W)
    proposed = _load(W / "11_draft" / "proposed_features.json", [])

    L = [f"# Christianese lexicon run report", "",
         f"Generated {dt.datetime.now().isoformat(timespec='minutes')} · lexicon-miner {__version__}"
         + (" · **DRY RUN (fake model outputs)**" if ctx.dry_run else ""), "",
         "## Corpus", "", "| stat | value |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(ingest.items())]
    L += ["", f"## Top {n} terms by final score", "",
          "| # | term | seeker asks (want/avoid) | z vs English | top tradition | polysemy | drift | contested | negation |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in ranked[:n]:
        L.append(f"| {r['rank']} | {r['term']} | {r['seeker_want']}/{r['seeker_avoid']} | {_fmt(r['z_english'])} | "
                 f"{r['top_tradition'] or '–'} | {_fmt(r['polysemy'])} | {_fmt(r['drift'])} | "
                 f"{_fmt(r['contestedness'])} | {_fmt(r['negation_rate'])} |")

    def top_by(field, label):
        rows = sorted([r for r in ranked if r.get(field) is not None], key=lambda r: -r[field])[:15]
        return ["", f"## Most {label}", "", ", ".join(f"{r['term']} ({r[field]:.2f})" for r in rows) or "–"]
    L += top_by("drift", "tradition-dependent (same word, different meaning by tradition)")
    L += top_by("contestedness", "contested (approving vs critical usage)")
    L += top_by("negation_rate", "often negated (\"not ___\")")
    shib = sorted([r for r in ranked if r["top_tradition"]], key=lambda r: -r["top_tradition_z"])[:20]
    L += ["", "## Tradition markers (shibboleths)", "",
          ", ".join(f"{r['term']} → {r['top_tradition']} (z={r['top_tradition_z']:.1f})" for r in shib) or "–"]
    L += ["", "## Grounding examples", ""]
    for r in [r for r in ranked if r["observables"]][:12]:
        L.append(f"- **{r['term']}**: " + ", ".join(f"{o['observable']} (+{o['log2_lift']:.1f})" for o in r["observables"]))
    L += ["", "## Drafted lexicon", "", f"- entries drafted: {len(lex)}",
          f"- low-evidence entries (count < 5): {sum(1 for e in lex if e['provenance']['low_evidence'])}",
          f"- proposed new features (need review): {len(proposed)}" + (f": {', '.join(proposed[:20])}" if proposed else "")]
    L += ["", "## Dimensions check", "", "| cluster | name | size | purity | categories |", "|---|---|---|---|---|"]
    for c in dims.get("clusters", []):
        L.append(f"| {c['id']} | {c['name']} | {c['size']} | {c['purity']} | {json.dumps(c['categories'])} |")
    L += ["", "## Coverage on held-out seeker requests", ""]
    if cov.get("coverage") is not None:
        L.append(f"**{100 * cov['coverage']:.1f}%** of {cov['holdout_attributes']} requested attributes are recognized.")
        L.append("")
        L.append("By category: " + ", ".join(f"{k} {100 * v:.0f}%" for k, v in cov["by_category"].items()))
        L.append("")
        L.append("Top uncovered: " + ", ".join(f"{p} ({c})" for p, c in cov["top_uncovered"][:25]))
    else:
        L.append(cov.get("note", "not run"))
    L += ["", "## Model calls (cost & latency)", "",
          "| task | model | calls | ok rate | median ms | total min | repairs | prompt tok | completion tok |",
          "|---|---|---|---|---|---|---|---|---|"]
    for c in calls:
        L.append(f"| {c['task']} | {c['model']} | {c['calls']} | {c['ok_rate']} | {c['median_ms']} | {c['total_min']} | "
                 f"{c['repairs']} | {c['prompt_tokens']} | {c['completion_tokens']} |")
    L += ["", "## Prompt versions", "",
          f"{prompts.SEEKER_EXTRACT_VERSION}, {prompts.STANCE_VERSION}, {prompts.DRAFT_VERSION}, {prompts.DIMENSION_VERSION}"]

    d = ctx.stage_dir(NAME)
    (d / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    export = {"schema_version": "lexicon.v1", "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "dry_run": ctx.dry_run, "entries": lex, "coverage": {k: v for k, v in cov.items() if k != "top_uncovered"}}
    out_dir = W / "out"
    write_json(out_dir / "lexicon.json", export)
    write_json(out_dir / "call_stats.json", calls)
    if not ctx.cfg["privacy"].get("retain_clean_docs", True):
        (W / "01_ingest" / "docs.jsonl").unlink(missing_ok=True)
    log.info("report written to %s", d / "report.md")
    return {"report": str(d / "report.md"), "lexicon": str(out_dir / "lexicon.json")}
