#!/usr/bin/env bash
# Start the platform. Serves the API and the console on one port.
# HOST and PORT are honoured for compatibility with the previous version.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/_python.sh"
exec "$(resolve_python "$ROOT")" "$ROOT/cip.py" run \
  --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
