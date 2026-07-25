#!/usr/bin/env bash
# Start the platform. Serves the API and the console on one port.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/_python.sh"
PY="$(resolve_python "$ROOT")"
cd "$ROOT/backend"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
echo "==> http://$HOST:$PORT   (Ctrl-C to stop)"
exec "$PY" -m uvicorn ksp_cip.interface.api.main:get_app --factory --host "$HOST" --port "$PORT" "$@"
