#!/usr/bin/env bash
# Hot-reload development: API on :8000, Vite console on :5173 proxying to it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/_python.sh"
exec "$(resolve_python "$ROOT")" "$ROOT/cip.py" dev "$@"
