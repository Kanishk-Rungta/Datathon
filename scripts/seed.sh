#!/usr/bin/env bash
# Generate the synthetic dataset and build all derived intelligence.
#   scripts/seed.sh [cases] [months] [--reset]
# Positional arguments are kept for compatibility; cip.py takes named ones.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/_python.sh"

CASES="${1:-4200}"
MONTHS="${2:-30}"
RESET=()
[[ "${3:-}" == "--reset" ]] && RESET=(--reset)

exec "$(resolve_python "$ROOT")" "$ROOT/cip.py" seed \
  --cases "$CASES" --months "$MONTHS" "${RESET[@]}"
