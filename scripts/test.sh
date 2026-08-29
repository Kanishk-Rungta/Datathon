#!/usr/bin/env bash
# Run the test suite. Pass -m "not slow" to skip the seeded integration tests.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/_python.sh"
exec "$(resolve_python "$ROOT")" "$ROOT/cip.py" test "$@"
