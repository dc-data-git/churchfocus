"""Offline eval runner (EVAL_PLAN §2–3). No network — resolve() name/alias path + suite metrics.

Usage:
  python -m eval.run
Writes eval/results.md
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Point at small KB for deterministic offline eval
os.environ.setdefault("DENOM_KB_PATH", str(ROOT / "tests" / "fixtures" / "kb_small.json"))
os.environ.setdefault("DATA_DIR", str(ROOT / "data" / "eval_run"))

from app.config import get_settings  # noqa: E402
from app.stage1.denomination import resolve  # noqa: E402

get_settings.cache_clear()


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def eval_denoms(path: Path) -> dict:
    rows = _load_jsonl(path)
    top1_ok = 0
    confident = 0
    confident_ok = 0
    unknown = 0
    by_kind: Counter = Counter()
    details = []

    for row in rows:
        guess = resolve(
            {
                "name": row["name"],
                "website": row.get("website"),
                "address": row.get("city", ""),
                "types": ["church"],
            }
        )
        conf = guess.confidence
        exp_id = row.get("expected_id")
        exp_label = (row.get("expected_label") or "").lower()
        ok = False
        if exp_id:
            ok = guess.denomination_id == exp_id
        elif exp_label:
            ok = exp_label in guess.label.lower()
        if ok:
            top1_ok += 1
        if conf >= 0.8:
            confident += 1
            if ok:
                confident_ok += 1
        if guess.method == "unknown" or conf <= 0.4:
            unknown += 1
        by_kind[row.get("kind", "?")] += 1
        details.append(
            {
                "name": row["name"],
                "expected": exp_id or exp_label,
                "got": guess.denomination_id or guess.label,
                "confidence": conf,
                "method": guess.method,
                "ok": ok,
            }
        )

    n = len(rows) or 1
    return {
        "n": len(rows),
        "top1_accuracy": top1_ok / n,
        "confident_n": confident,
        "confident_accuracy": (confident_ok / confident) if confident else None,
        "unknown_rate": unknown / n,
        "by_kind": dict(by_kind),
        "details": details,
    }


def pytest_bar() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    return {"exit": proc.returncode, "summary": tail}


def write_results(denom: dict, pytest_info: dict) -> Path:
    out = ROOT / "eval" / "results.md"
    lines = [
        "# Eval results",
        "",
        f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_",
        "",
        "## 1. CI bar",
        f"- pytest: `{pytest_info['summary']}` (exit {pytest_info['exit']})",
        "",
        "## 2. Denomination resolve (`eval/denoms.jsonl`)",
        f"- N = {denom['n']} (seed set; expand via H3 with official locator labels)",
        f"- Top-1 accuracy: **{denom['top1_accuracy']:.0%}**",
        (
            f"- Confident (≥0.8) accuracy: **{denom['confident_accuracy']:.0%}** "
            f"(n={denom['confident_n']})"
            if denom["confident_accuracy"] is not None
            else f"- Confident (≥0.8): n={denom['confident_n']} (none)"
        ),
        f"- Unknown / low-confidence rate: **{denom['unknown_rate']:.0%}**",
        f"- By kind: `{denom['by_kind']}`",
        "",
        "### Misses",
    ]
    misses = [d for d in denom["details"] if not d["ok"]]
    if not misses:
        lines.append("- (none)")
    else:
        for d in misses[:20]:
            lines.append(
                f"- {d['name']}: expected `{d['expected']}` → `{d['got']}` "
                f"(conf={d['confidence']:.2f}, method={d['method']})"
            )
    lines += [
        "",
        "## 3. Performance / cost",
        "- Fill from `data/logs/calls.jsonl` after a live Stage 1–3 run.",
        "- Targets: Stage 1 < 60 s; Stage 2 < 2 min/church; Stage 3 minutes + $/church.",
        "",
        "## Notes",
        "- This run uses `tests/fixtures/kb_small.json` (5 groups) so name/alias coverage is limited.",
        "- Point `DENOM_KB_PATH` at the full KB and expand `denoms.jsonl` (H3) for submission numbers.",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> None:
    denom = eval_denoms(ROOT / "eval" / "denoms.jsonl")
    pytest_info = pytest_bar()
    path = write_results(denom, pytest_info)
    print(f"wrote {path}")
    print(f"denom top-1={denom['top1_accuracy']:.0%} confident={denom['confident_accuracy']}")
    print(f"pytest: {pytest_info['summary']}")


if __name__ == "__main__":
    main()
