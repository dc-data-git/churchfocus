"""End-to-end dry run on the synthetic corpus plus unit checks on the
deterministic pieces. No model server required."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from lexicon.cli import main
from lexicon.review import cohen_kappa
from lexicon.stages.s04_keyness import log_odds_z
from lexicon.stages.s13_coverage import covered
from lexicon.text import negated_at, scrub, tokenize

ROOT = Path(__file__).resolve().parents[1]


def test_scrub_and_tokenize():
    t = scrub("Email me a@b.com or @pastor_joe, call 316-555-1212, see https://x.org", {})
    assert "@" not in t.replace("[user]", "") and "555" not in t and "x.org" not in t
    assert tokenize("Spirit-filled, Bible-believing church's") == ["spirit-filled", "bible-believing", "church's"]


def test_negation():
    toks = tokenize("we want a church that is not legalistic")
    assert negated_at(toks, toks.index("legalistic"))


def test_log_odds_direction():
    assert log_odds_z(50, 1000, 5, 1000, 1.0, 100) > 2
    assert log_odds_z(5, 1000, 50, 1000, 1.0, 100) < -2


def test_coverage_matching():
    lex = [tokenize("contemporary worship"), tokenize("altar call")]
    assert covered("contemporary worship", lex)
    assert covered("a strong contemporary worship band", lex)
    assert not covered("kids ministry", lex)


def test_kappa():
    assert cohen_kappa([1, 1, 0, 0], [1, 1, 0, 0]) == 1.0


def test_dry_run_end_to_end(tmp_path):
    from tests.make_fixture import build
    proj = tmp_path / "proj"
    for d in ("data", "schemas"):
        shutil.copytree(ROOT / d, proj / d)
    build(proj / "data" / "raw")
    cfg = yaml.safe_load((ROOT / "config.example.yaml").read_text())
    cfg["shortlist"]["size"] = 80
    cfg["draft"]["top_n"] = 30
    (proj / "config.yaml").write_text(yaml.safe_dump(cfg))
    main(["-c", str(proj / "config.yaml"), "run", "--dry-run"])
    work = proj / "work_dryrun"
    stats = json.loads((work / "01_ingest" / "stats.json").read_text())
    assert stats["kept_church"] > 0 and stats["kept_seeker"] > 0 and stats["seeker_holdout"] > 0
    docs = (work / "01_ingest" / "docs.jsonl").read_text()
    assert "SHOULD_BE_DROPPED" not in docs and "@someone" not in docs
    ranked = [json.loads(line) for line in (work / "10_rank" / "ranked.jsonl").read_text().splitlines()]
    terms = {r["term"] for r in ranked}
    assert "altar call" in terms and "traditional worship" in terms
    trad = next(r for r in ranked if r["term"] == "traditional")
    assert trad["count"] > 0
    lex = json.loads((work / "out" / "lexicon.json").read_text())
    rejected = (work / "11_draft" / "rejected.jsonl").read_text().splitlines()
    assert len(lex["entries"]) + len(rejected) == 30 and len(lex["entries"]) > 0 and lex["dry_run"] is True
    assert all(e["provenance"]["vetted"] is False for e in lex["entries"])
    assert (work / "14_report" / "report.md").exists()
    # resumability: second run skips everything
    main(["-c", str(proj / "config.yaml"), "run", "--dry-run"])


def test_review_roundtrip(tmp_path):
    import csv
    from lexicon import review
    from lexicon.util import Ctx, write_json, write_jsonl
    work = tmp_path / "work"

    def entry(term, types, want=0, drift=0.0):
        return {"term": term, "category": "worship", "term_types": types,
                "senses": [{"gloss": "g", "traditions": ["all"], "features": [{"feature": "worship.altar_call", "value": "regular"}]}],
                "disambiguation_question": "", "neutral_options": [],
                "evidence_stats": {"seeker_want": want, "seeker_avoid": 0, "drift": drift, "rank": 1},
                "provenance": {"vetted": False}}
    drafts = [entry("altar call", ["descriptive"], want=9), entry("woke", ["politically_coded"]),
              entry("traditional", ["polysemous"], drift=0.5), entry("liturgy", ["descriptive"], want=1)]
    write_json(work / "11_draft" / "lexicon_draft.json", drafts)
    write_jsonl(work / "06_contexts" / "contexts.jsonl", [{"term": "altar call", "contexts": [{"text": "x"}]}])
    ctx = Ctx(cfg={"review": {"tier_b_size": 1, "audit_size": 1}}, root=tmp_path, work=work, dry_run=True)
    tiers = review.assign_tiers(drafts, {**review.DEFAULTS, "tier_b_size": 1, "audit_size": 1})
    assert tiers == {"woke": "A", "traditional": "A", "altar call": "B", "liturgy": "audit"}
    _, counts = review.plan(ctx)
    assert counts["A"] == 2 and counts["B"] == 1

    def fill(p, keep_woke):
        rows = list(csv.DictReader(open(p, encoding="utf-8")))
        for r in rows:
            r.update(keep=keep_woke if r["term"] == "woke" else "y", neutral_ok="y", types_ok="y", features_ok="y")
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        return str(p)
    a = fill(review.export(ctx, "a"), "y")                       # A, B, audit
    b = fill(review.export(ctx, "b", ["A"]), "n")                # second reviewer, tier A only
    out = review.apply(ctx, [a, b])
    got = {e["term"]: e["provenance"] for e in json.loads(out.read_text())["entries"]}
    assert "woke" not in got                                     # rejected by reviewer b
    assert got["traditional"]["vetted"] and got["traditional"]["reviewers"] == ["a", "b"]
    assert got["altar call"]["vetted"] and got["liturgy"]["vetted"]
    s = json.loads((tmp_path / "review" / "summary.json").read_text())
    assert s["audit"]["reviewed"] == 1 and s["audit"]["error_rate"] == 0.0
    # tier A with only one reviewer is not vetted
    out = review.apply(ctx, [a])
    got = {e["term"]: e["provenance"] for e in json.loads(out.read_text())["entries"]}
    assert got["traditional"]["vetted"] is False
