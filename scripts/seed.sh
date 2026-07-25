#!/usr/bin/env bash
# Generate the synthetic dataset and build all derived intelligence.
#   scripts/seed.sh [cases] [months] [--reset]
# Re-running is safe; --reset rebuilds from scratch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/_python.sh"
PY="$(resolve_python "$ROOT")"
cd "$ROOT/backend"

CASES="${1:-4200}"
MONTHS="${2:-30}"
RESET=""
[[ "${3:-}" == "--reset" ]] && RESET="--reset"

exec "$PY" -m ksp_cip.cli seed --cases "$CASES" --months "$MONTHS" $RESET
