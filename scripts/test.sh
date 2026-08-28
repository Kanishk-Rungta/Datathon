#!/usr/bin/env bash
# Run the test suite.  Pass -m "not slow" to skip the seeded integration tests.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/_python.sh"
PY="$(resolve_python "$ROOT")"
cd "$ROOT/backend"
exec "$PY" -m pytest "$@"
