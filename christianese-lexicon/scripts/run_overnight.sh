#!/usr/bin/env bash
# Run the full pipeline unattended. Safe to re-run: finished stages are skipped
# and every model call is cached.
#   scripts/run_overnight.sh            # uses config.yaml
#   scripts/run_overnight.sh other.yaml
set -euo pipefail
cd "$(dirname "$0")/.."
CFG="${1:-config.yaml}"
[ -d .venv ] && source .venv/bin/activate
python -m lexicon -c "$CFG" doctor
nohup python -m lexicon -c "$CFG" run > run_overnight.out 2>&1 &
echo "started pid $! ; follow with: tail -f run_overnight.out   |   status: python -m lexicon -c $CFG status"
