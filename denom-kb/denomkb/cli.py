"""Command line.

  python -m denomkb doctor   [-c config.yaml]       check workbook, model server, web access
  python -m denomkb run      [-c config.yaml] [--max N] [--only id1,id2] [--force] [--dry-run] [--offline]
  python -m denomkb status   [-c config.yaml]
  python -m denomkb export   [-c config.yaml]       rebuild outputs from finished groups (also runs automatically)

Groups are processed largest-first. Each finished group is saved immediately; outputs are
re-exported every `export_every` groups and at the end, so Ctrl-C loses at most one group.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import export
from .llm import LLM
from .pipeline import Runner
from .util import load_config, read_json
from .web import Web
from .workbook import load

log = logging.getLogger("denomkb")


def _setup(args):
    cfg, root = load_config(args.config)
    if getattr(args, "dry_run", False):
        cfg["work_dir"] = cfg["work_dir"].rstrip("/") + "_dryrun"
    work = (root / cfg["work_dir"]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    kb = load(root / cfg["workbook"])
    web = Web(cfg.get("web", {}), work / "web_cache", offline=getattr(args, "offline", False))
    llm = LLM(cfg["llm"], work, dry_run=getattr(args, "dry_run", False))
    return cfg, root, work, kb, web, llm


def cmd_run(args):
    cfg, root, work, kb, web, llm = _setup(args)
    fh = logging.FileHandler(work / "run.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)
    if args.max:
        cfg.setdefault("groups", {})["max"] = args.max
    if args.only:
        cfg.setdefault("groups", {})["only"] = args.only.split(",")
    r = Runner(cfg, root, kb, web, llm, dry_run=args.dry_run)
    groups = r.order()
    every = cfg.get("export_every", 5)
    log.info("processing %d groups (largest first); work dir %s", len(groups), work)
    t0 = time.time()
    try:
        for i, g in enumerate(groups, 1):
            try:
                r.run_group(g, force=args.force)
            except Exception as e:  # noqa: BLE001  one bad group must not stop an overnight run
                log.exception("group %s failed: %s", g["id"], e)
            if i % every == 0:
                export.run(cfg, root, kb, args.dry_run)
                log.info("progress %d/%d groups, %.0f min elapsed", i, len(groups), (time.time() - t0) / 60)
    finally:
        res = export.run(cfg, root, kb, args.dry_run)
        print(f"\nExported: {res}")


def cmd_export(args):
    cfg, root, work, kb, web, llm = _setup(args)
    print(export.run(cfg, root, kb, getattr(args, "dry_run", False)))


def cmd_status(args):
    cfg, root, work, kb, web, llm = _setup(args)
    r = Runner(cfg, root, kb, web, llm)
    n_done = 0
    for g in r.order():
        p = work / "groups" / g["id"] / "done.json"
        if p.exists():
            n_done += 1
            s = read_json(p)
            print(f"[x] {g['census_name'][:48]:48s} fill {s['fill']:3d} confirm {s['confirm']:3d} conflict {s['conflict']:2d} "
                  f"n/a {s['not_applicable']:3d} pages {s['pages']:2d}")
        else:
            print(f"[ ] {g['census_name'][:48]}")
    print(f"\n{n_done} done")


def cmd_doctor(args):
    cfg, root, work, kb, web, llm = _setup(args)
    ok = True
    print(f"workbook: {len(kb.groups)} groups, {len(kb.fields)} fields, {sum(1 for v in kb.values.values() if v and v != 'Unknown')} known values")
    ua = cfg.get("web", {}).get("user_agent", "")
    if "contact" not in ua.lower() and "@" not in ua:
        print("web.user_agent: add a contact (email or URL). Wikipedia asks for one and rate-limits anonymous bots.")
    try:
        from .discover import wikipedia
        p = wikipedia(web, "Southern Baptist Convention")
        print("wikipedia:", "OK" if p else "FAILED")
        ok &= bool(p)
    except Exception as e:  # noqa: BLE001
        print("wikipedia: FAILED", e)
        ok = False
    try:
        print("model server:", llm.ping())
        v = llm.complete_json("doctor", [{"role": "user", "content": 'Return {"ok": true}'}],
                              {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
        print(f"chat model {cfg['llm']['chat_model']}:", "OK" if v else "FAILED")
        ok &= bool(v)
    except Exception as e:  # noqa: BLE001
        print("model server: FAILED", e)
        ok = False
    print("\nREADY" if ok else "\nNOT READY")
    sys.exit(0 if ok else 1)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="denomkb")
    p.add_argument("-c", "--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--max", type=int, help="process at most N groups (largest first)")
    r.add_argument("--only", help="comma-separated database ids")
    r.add_argument("--force", action="store_true", help="re-process groups already done (model calls stay cached)")
    r.add_argument("--dry-run", action="store_true", help="fake model outputs; separate work dir")
    r.add_argument("--offline", action="store_true", help="use only cached web pages")
    r.set_defaults(fn=cmd_run)
    for name, fn in (("status", cmd_status), ("export", cmd_export), ("doctor", cmd_doctor)):
        sp = sub.add_parser(name)
        sp.add_argument("--dry-run", action="store_true")
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    args.fn(args)
