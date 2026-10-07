"""01 ingest: read raw files, drop identity fields, scrub PII, dedupe, split holdout.

Input files in input_dir: *.jsonl or *.csv with columns
  text (required), side ("seeker" | "church", required),
  tradition (optional), source (optional), date (optional), id (optional).
Any other field (author, username, ...) is dropped on read.
"""
from __future__ import annotations

import csv
import hashlib
import logging
from collections import Counter

from ..text import scrub, tokenize
from ..util import Ctx, read_jsonl, write_json, write_jsonl

log = logging.getLogger("lexicon.ingest")
NAME = "01_ingest"
KEEP = {"id", "text", "side", "tradition", "source", "date"}


def _read_raw(ctx: Ctx):
    src = ctx.path("input_dir")
    files = sorted(list(src.glob("**/*.jsonl")) + list(src.glob("**/*.csv")))
    if not files:
        raise SystemExit(f"No .jsonl or .csv files found in {src}")
    for fp in files:
        if fp.suffix == ".jsonl":
            rows = read_jsonl(fp)
        else:
            f = open(fp, encoding="utf-8", newline="")
            rows = csv.DictReader(f)
        for r in rows:
            r["_file"] = fp.name
            yield r


def run(ctx: Ctx) -> dict:
    priv = ctx.cfg["privacy"]
    frac = ctx.cfg["split"]["seeker_holdout_fraction"]
    seen = set()
    stats = Counter()
    out = []
    for r in _read_raw(ctx):
        stats["read"] += 1
        side = (r.get("side") or "").strip().lower()
        text = (r.get("text") or "").strip()
        if side not in ("seeker", "church") or not text:
            stats["dropped_bad_row"] += 1
            continue
        text = scrub(text, priv)
        toks = tokenize(text)
        if len(toks) < 5:
            stats["dropped_short"] += 1
            continue
        h = hashlib.sha1(" ".join(toks).encode()).hexdigest()
        if h in seen:
            stats["dropped_duplicate"] += 1
            continue
        seen.add(h)
        tradition = (r.get("tradition") or "").strip().lower() or None
        holdout = side == "seeker" and (int(h[:8], 16) / 0xFFFFFFFF) < frac
        out.append({
            "id": str(r.get("id") or h[:16]),
            "side": side,
            "tradition": tradition,
            "source": (r.get("source") or r["_file"]),
            "date": r.get("date") or None,
            "text": text,
            "holdout": holdout,
        })
        stats[f"kept_{side}"] += 1
        if holdout:
            stats["seeker_holdout"] += 1
        if tradition:
            stats[f"tradition:{tradition}"] += 1
    n_syn = sum(1 for o in out if o["source"] == "synthetic")
    if n_syn and not ctx.dry_run:
        log.warning("%d SYNTHETIC test docs are in the input (source=synthetic). Remove data/raw/synthetic.jsonl "
                    "before a real run.", n_syn)
    stats["synthetic_docs"] = n_syn
    d = ctx.stage_dir(NAME)
    write_jsonl(d / "docs.jsonl", out)
    summary = dict(stats)
    write_json(d / "stats.json", summary)
    log.info("ingest: %s", summary)
    return summary


def load_docs(ctx: Ctx, include_holdout: bool = False):
    for doc in read_jsonl(ctx.work / NAME / "docs.jsonl"):
        if doc["holdout"] and not include_holdout:
            continue
        yield doc


def load_holdout(ctx: Ctx):
    for doc in read_jsonl(ctx.work / NAME / "docs.jsonl"):
        if doc["holdout"]:
            yield doc
