"""Command line.

  python -m lexicon doctor  [-c config.yaml]            check config, input files, model server
  python -m lexicon run     [-c config.yaml] [--dry-run] [--from STAGE] [--only STAGE] [--to STAGE]
  python -m lexicon status  [-c config.yaml]
  python -m lexicon review plan
  python -m lexicon review export --reviewer NAME [--tiers A,B,audit]
  python -m lexicon review kappa A.csv B.csv
  python -m lexicon review apply A.csv [B.csv ...]

Runs are resumable: a finished stage writes <work>/<stage>/_done.json and is
skipped next time. --from re-runs a stage and everything after it.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import review
from .llm import LLM
from .stages import STAGES, USES_LLM
from .util import Ctx, load_config, read_json, write_json

log = logging.getLogger("lexicon")


def _ctx(args) -> Ctx:
    cfg, root = load_config(args.config)
    work = (root / cfg["work_dir"]).resolve()
    if getattr(args, "dry_run", False):
        work = work.parent / (work.name + "_dryrun")
    work.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(cfg=cfg, root=root, work=work, dry_run=getattr(args, "dry_run", False))
    ctx.llm = LLM(cfg["llm"], work, dry_run=ctx.dry_run)
    return ctx


def _stage_names():
    return [s.NAME for s in STAGES]


def _resolve(name: str | None) -> int | None:
    if name is None:
        return None
    for i, s in enumerate(STAGES):
        if s.NAME == name or s.NAME.split("_", 1)[0] == name.zfill(2) or s.NAME.split("_", 1)[1] == name:
            return i
    raise SystemExit(f"unknown stage {name}; choose from {_stage_names()}")


def cmd_run(args):
    ctx = _ctx(args)
    fh = logging.FileHandler(ctx.work / "run.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)
    start = _resolve(args.from_) or 0
    end = _resolve(args.to)
    end = len(STAGES) - 1 if end is None else end
    only = _resolve(args.only)
    if only is not None:
        start = end = only
    if args.from_ is not None:
        for s in STAGES[start:]:
            done = ctx.work / s.NAME / "_done.json"
            done.unlink(missing_ok=True)
    log.info("run %s stages %s..%s dry_run=%s work=%s", "", STAGES[start].NAME, STAGES[end].NAME, ctx.dry_run, ctx.work)
    for s in STAGES[start:end + 1]:
        done = ctx.work / s.NAME / "_done.json"
        if done.exists() and only is None:
            log.info("skip  %s (done)", s.NAME)
            continue
        t0 = time.time()
        log.info("start %s%s", s.NAME, "  [model calls]" if s.NAME in USES_LLM else "")
        summary = s.run(ctx)
        write_json(done, {"summary": summary, "seconds": round(time.time() - t0, 1),
                          "finished": time.strftime("%Y-%m-%d %H:%M:%S")})
        log.info("done  %s in %.1fs", s.NAME, time.time() - t0)
    print(f"\nFinished. Report: {ctx.work / '14_report' / 'report.md'}\nLexicon: {ctx.work / 'out' / 'lexicon.json'}")


def cmd_status(args):
    ctx = _ctx(args)
    for s in STAGES:
        done = ctx.work / s.NAME / "_done.json"
        if done.exists():
            d = read_json(done)
            print(f"[x] {s.NAME:16s} {d['seconds']:>8}s  {json.dumps(d['summary'])[:120]}")
        else:
            print(f"[ ] {s.NAME}")


def cmd_doctor(args):
    ctx = _ctx(args)
    ok = True
    inp = ctx.path("input_dir")
    files = list(inp.glob("**/*.jsonl")) + list(inp.glob("**/*.csv"))
    print(f"input_dir: {inp} -> {len(files)} file(s)")
    ok &= bool(files)
    for key in ("seed_terms", "observables", "features", "schema"):
        p = ctx.path(key)
        print(f"{key}: {p} {'OK' if p.exists() else 'MISSING'}")
        ok &= p.exists()
    try:
        print("model server:", ctx.llm.ping())
        for m in {ctx.cfg['llm']['chat_model'], ctx.cfg['llm']['small_chat_model']}:
            v = ctx.llm.complete_json("doctor", [{"role": "user", "content": 'Return {"ok": true}'}],
                                      {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                                      model=m)
            print(f"chat model {m}: {'OK' if v else 'FAILED'}")
            ok &= bool(v)
        e = ctx.llm.embed(["altar call", "worship band"])
        print(f"embed model {ctx.cfg['llm']['embed_model']}: OK (dim {e.shape[1]})")
    except Exception as ex:  # noqa: BLE001
        print("model server: FAILED:", ex)
        ok = False
    print("\nREADY" if ok else "\nNOT READY")
    sys.exit(0 if ok else 1)


def cmd_review(args):
    ctx = _ctx(args)
    if args.action == "plan":
        out, counts = review.plan(ctx)
        print(out)
        print(json.dumps(counts, indent=2))
    elif args.action == "export":
        tiers = args.tiers.split(",") if args.tiers else None
        print(review.export(ctx, args.reviewer or "reviewer", tiers))
    elif args.action == "kappa":
        print(json.dumps(review.kappa(args.files[0], args.files[1]), indent=2))
    elif args.action == "apply":
        out = review.apply(ctx, args.files)
        from .stages import s13_coverage
        res = s13_coverage.run(ctx, lexicon_path=out)
        summary = read_json(ctx.root / "review" / "summary.json")
        print(out)
        print(json.dumps({k: v for k, v in summary.items() if k not in ("rejected_by_reviewers", "under_reviewed")}, indent=2))
        print(f"rejected by reviewers: {len(summary['rejected_by_reviewers'])}   "
              f"still need reviewers: {len(summary['under_reviewed'])}   coverage after review: {res.get('coverage')}")


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(prog="lexicon")
    p.add_argument("-c", "--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true", help="no model calls; fake outputs; separate work dir")
    r.add_argument("--from", dest="from_", help="re-run from this stage (e.g. 07 or polysemy)")
    r.add_argument("--to", help="stop after this stage")
    r.add_argument("--only", help="run just this stage")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    rv = sub.add_parser("review")
    rv.add_argument("action", choices=["plan", "export", "kappa", "apply"])
    rv.add_argument("files", nargs="*")
    rv.add_argument("--reviewer")
    rv.add_argument("--tiers", help="comma list of tiers to export, default A,B,audit (second reviewer: A)")
    rv.set_defaults(fn=cmd_review)
    for sp in (sub.choices["status"], rv):
        sp.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    args.fn(args)
