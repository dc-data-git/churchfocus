#!/usr/bin/env bash
# Run every group unattended, largest first. Safe to re-run: finished groups are skipped,
# model calls and web pages are cached.
#   bash scripts/run_overnight.sh              # uses config.yaml
#   bash scripts/run_overnight.sh other.yaml
set -euo pipefail
cd "$(dirname "$0")/.."
CFG="${1:-config.yaml}"
[ -d .venv ] && source .venv/bin/activate
python -m denomkb -c "$CFG" doctor
nohup python -m denomkb -c "$CFG" run > run_overnight.out 2>&1 &
echo "started pid $! ; follow: tail -f run_overnight.out ; progress: python -m denomkb -c $CFG status"
