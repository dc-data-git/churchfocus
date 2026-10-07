# Eval results

_Generated 2026-10-07T19:15:10+00:00_

## 1. CI bar
- pytest: `114 passed in 28.09s` (exit 0)

## 2. Denomination resolve (`eval/denoms.jsonl`)
- N = 30 (seed set; expand via H3 with official locator labels)
- Top-1 accuracy: **77%**
- Confident (≥0.8) accuracy: **100%** (n=8)
- Unknown / low-confidence rate: **73%**
- By kind: `{'denominational': 13, 'abbrev': 2, 'nondenom': 11, 'secret': 4}`

### Misses
- Hope Community Church: expected `non-denominational` → `Unknown (likely independent)` (conf=0.40, method=unknown)
- Grace Point: expected `sbc` → `Unknown (likely independent)` (conf=0.40, method=unknown)
- Redeemer Fellowship: expected `sbc` → `Unknown (likely independent)` (conf=0.40, method=unknown)
- Christ Church Anglican: expected `` → `Unknown (likely independent)` (conf=0.40, method=unknown)
- Lakewood Church: expected `non-denominational` → `Unknown (likely independent)` (conf=0.40, method=unknown)
- Co-Cathedral of the Sacred Heart: expected `catholic` → `Unknown (likely independent)` (conf=0.40, method=unknown)
- Vintage Church: expected `sbc` → `Unknown (likely independent)` (conf=0.40, method=unknown)

## 3. Performance / cost
- Fill from `data/logs/calls.jsonl` after a live Stage 1–3 run.
- Targets: Stage 1 < 60 s; Stage 2 < 2 min/church; Stage 3 minutes + $/church.

## Notes
- This run uses `tests/fixtures/kb_small.json` (5 groups) so name/alias coverage is limited.
- Point `DENOM_KB_PATH` at the full KB and expand `denoms.jsonl` (H3) for submission numbers.
