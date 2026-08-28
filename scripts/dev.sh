#!/usr/bin/env bash
# Hot-reload development: API on :8000, Vite console on :5173 proxying to it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/_python.sh"
PY="$(resolve_python "$ROOT")"

(cd "$ROOT/backend" && "$PY" -m uvicorn ksp_cip.interface.api.main:get_app --factory --reload --port 8000) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT
(cd "$ROOT/frontend" && npm run dev)
