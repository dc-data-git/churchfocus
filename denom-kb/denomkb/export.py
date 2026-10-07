"""Collect every finished group's proposals and write the outputs.

work/out/
  US_Religious_Groups_filled.xlsx   copy of the input with fills (green), conflicts (red, value unchanged),
                                    not-applicable (grey), Proposals + Run Info sheets, appended Evidence/Sources
  proposals.csv                     one row per proposed change (review this)
  denominations_kb.json             merged knowledge base for the MCP server (every value with its evidence)
  report.md                         coverage before/after, by layer, conflicts, model cost/latency
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from . import prompts
from .util import read_json, write_json
from .verify import applied as _applied
from .workbook import KB, LAYER_SHEETS, is_unknown, write_updated


def collect(work: Path) -> tuple[list[dict], dict]:
    props, done = [], {}
    gdir = work / "groups"
    if not gdir.exists():
        return props, done
    for d in sorted(gdir.iterdir()):
        if (d / "done.json").exists():
            done[d.name] = read_json(d / "done.json")
            props.extend(read_json(d / "proposals.json"))
    return props, done


def _source_id(url: str) -> str:
    return "dkb_" + hashlib.sha1(url.encode()).hexdigest()[:10]


def call_stats(work: Path) -> list[dict]:
    p = work / "calls.jsonl"
    if not p.exists():
        return []
    agg = defaultdict(lambda: {"calls": 0, "ok": 0, "ms": [], "repairs": 0, "pt": 0, "ct": 0})
    for line in p.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        a = agg[(r["task"], r["model"])]
        a["calls"] += 1
        a["ok"] += int(r.get("ok", False))
        a["ms"].append(r.get("ms", 0))
        a["repairs"] += r.get("repairs", 0)
        a["pt"] += r.get("prompt_tokens", 0)
        a["ct"] += r.get("completion_tokens", 0)
    return [{"task": t, "model": m, "calls": a["calls"], "ok_rate": round(a["ok"] / a["calls"], 3),
             "median_s": round(statistics.median(a["ms"]) / 1000, 1) if a["ms"] else 0,
             "total_min": round(sum(a["ms"]) / 60000, 1), "repairs": a["repairs"], "prompt_tokens": a["pt"],
             "completion_tokens": a["ct"]} for (t, m), a in sorted(agg.items())]


def run(cfg: dict, root: Path, kb: KB, dry_run: bool) -> dict:
    work = (root / cfg["work_dir"]).resolve()
    out = work / "out"
    out.mkdir(parents=True, exist_ok=True)
    all_props, done = collect(work)
    props = [p for p in all_props if _applied(p)]

    # source register for new URLs
    new_sources, seen = [], {s.get("url") for s in kb.sources.values()}
    for p in all_props:
        if p["url"] and p["url"].startswith("http"):
            p["source_id"] = _source_id(p["url"])
            if p["url"] not in seen:
                seen.add(p["url"])
                new_sources.append({"id": p["source_id"], "title": p["url"], "url": p["url"],
                                    "type": {"official": "official_denomination", "register": "official_denomination",
                                             "wikipedia": "encyclopedia", "wikidata": "knowledge_graph"}.get(p["source_type"], p["source_type"]),
                                    "notes": "Added by denom-kb; verify before publishing."})

    cols = ["group_id", "field_id", "action", "old_value", "old_status", "new_value", "quote", "url", "source_type",
            "source_id", "confidence", "model", "checked_at"]
    with open(out / "proposals.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["group"] + cols + ["verify_verdict", "verify_reason", "verify_model", "applied",
                                                             "review_decision", "review_notes"], extrasaction="ignore")
        w.writeheader()
        for p in all_props:
            v = p.get("verify") or {}
            w.writerow({**p, "group": kb.group(p["group_id"])["census_name"], "verify_verdict": v.get("verdict", ""),
                        "verify_reason": v.get("reason", ""), "verify_model": v.get("model", ""),
                        "applied": "yes" if _applied(p) else "no", "review_decision": "", "review_notes": ""})

    # merged KB json
    by_gf = defaultdict(list)
    for p in props:
        by_gf[(p["group_id"], p["field_id"])].append(p)
    groups_json = []
    for g in kb.groups:
        fields = {}
        for fid, meta in kb.fields.items():
            old = kb.values.get((g["id"], fid), "")
            ev = kb.evidence.get((g["id"], fid), {})
            entry = {"value": None if is_unknown(old) else old, "status": ev.get("status") or ("unknown" if is_unknown(old) else "unsourced"),
                     "evidence": []}
            for p in by_gf.get((g["id"], fid), []):
                e = {"action": p["action"], "quote": p["quote"], "url": p["url"], "source_type": p["source_type"],
                     "confidence": p["confidence"], "model": p["model"], "checked_at": p["checked_at"]}
                if p["action"] == "fill":
                    entry["value"], entry["status"] = p["new_value"], "model_extracted_needs_review"
                elif p["action"] == "not_applicable":
                    entry["value"], entry["status"] = "Not applicable", "not_applicable"
                elif p["action"] == "confirm":
                    entry["status"] = "model_confirmed_needs_review"
                elif p["action"] == "conflict":
                    entry["status"] = "conflict_needs_review"
                    e["proposed_value"] = p["new_value"]
                entry["evidence"].append(e)
            fields[fid] = entry
        groups_json.append({"id": g["id"], "census_name": g["census_name"], "name": g["name"],
                            "census_2020": {"congregations": g["congregations"], "adherents": g["adherents"], "share": g["share"]},
                            "processed": g["id"] in done, "fields": fields})
    write_json(out / "denominations_kb.json", {"schema_version": "denom-kb.v1", "dry_run": dry_run,
                                               "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                               "groups": groups_json})

    calls = call_stats(work)
    run_info = {"generated": dt.datetime.now().isoformat(timespec="minutes"), "dry_run": dry_run,
                "groups_processed": len(done), "proposals": len(props), "model": cfg["llm"]["chat_model"],
                "prompt_versions": ", ".join(prompts.VERSIONS)}
    used_urls = {p["url"] for p in props}
    xlsx = write_updated(kb, props, [s for s in new_sources if s["url"] in used_urls],
                         out / "US_Religious_Groups_filled.xlsx", run_info)

    # report
    known_before = Counter()
    total = Counter()
    for (gid, fid), v in kb.values.items():
        layer = kb.fields[fid]["layer"]
        total[layer] += 1
        if not is_unknown(v):
            known_before[layer] += 1
    act = Counter((p["layer"], p["action"]) for p in props)
    L = ["# denom-kb run report", "", f"Generated {run_info['generated']}" + (" · **DRY RUN**" if dry_run else ""), "",
         f"Groups processed: **{len(done)}** of {len(kb.groups)} · proposals: **{len(all_props)}** "
         f"(applied after verification: **{len(props)}**)", "",
         "## Coverage by layer", "", "| layer | known before | + filled | + not applicable | confirmed | conflicts | known after |",
         "|---|---|---|---|---|---|---|"]
    for layer in LAYER_SHEETS:
        b, t = known_before[layer], total[layer]
        f, na = act[(layer, "fill")], act[(layer, "not_applicable")]
        L.append(f"| {layer} | {b} ({100 * b / t:.1f}%) | {f} | {na} | {act[(layer, 'confirm')]} | {act[(layer, 'conflict')]} "
                 f"| {b + f + na} ({100 * (b + f + na) / t:.1f}%) |")
    L += ["", "## Groups", "", "| group | Christian | Wikipedia (verdict) | site | pages | filled | confirmed | conflicts | N/A |",
          "|---|---|---|---|---|---|---|---|---|"]
    for g in kb.groups:
        s = done.get(g["id"])
        if s:
            L.append(f"| {g['census_name'][:45]} | {s['christian']} | {s.get('wikipedia') or '–'} ({s.get('wikipedia_verdict') or '–'}) | "
                     f"{(s.get('official_site') or '–')[:40]} | {s['pages']} | {s['fill']} | {s['confirm']} | {s['conflict']} | {s['not_applicable']} |")
    ver = [p for p in all_props if p.get("verify")]
    if ver:
        L += ["", "## Verification (does the quote support the value?)", "",
              "| action | source | checked | supported | partial | unsupported |", "|---|---|---|---|---|---|"]
        keys = sorted({(p["action"], p["source_type"]) for p in ver})
        for a, s in keys:
            vs = [p["verify"]["verdict"] for p in ver if p["action"] == a and p["source_type"] == s]
            n = len(vs)
            L.append(f"| {a} | {s} | {n} | {vs.count('supported')} ({100 * vs.count('supported') / n:.0f}%) | "
                     f"{vs.count('partial')} | {vs.count('unsupported')} |")
        unv = sum(1 for p in all_props if p["action"] in ("fill", "confirm", "conflict") and p["source_type"] != "wikidata"
                  and not p.get("verify"))
        L.append("")
        L.append(f"Verifier model: {', '.join(sorted({p['verify']['model'] for p in ver}))}. Not yet verified: {unv}.")
    conf = [p for p in props if p["action"] == "conflict"]
    L += ["", f"## Conflicts to review ({len(conf)})", ""]
    for p in conf[:60]:
        L.append(f"- **{kb.group(p['group_id'])['census_name']}** `{p['field_id']}`: existing \"{p['old_value']}\" vs "
                 f"sources \"{p['new_value']}\": \"{p['quote'][:160]}\" ({p['url']})")
    L += ["", "## Model calls", "", "| task | model | calls | ok rate | median s | total min | repairs | prompt tok | completion tok |",
          "|---|---|---|---|---|---|---|---|---|"]
    for c in calls:
        L.append(f"| {c['task']} | {c['model']} | {c['calls']} | {c['ok_rate']} | {c['median_s']} | {c['total_min']} | "
                 f"{c['repairs']} | {c['prompt_tokens']} | {c['completion_tokens']} |")
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return {"groups": len(done), "proposals": len(props), "xlsx": str(xlsx), "report": str(out / "report.md")}
