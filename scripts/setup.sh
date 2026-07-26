#!/usr/bin/env bash
# Install everything needed to run the platform locally.
#
# Creates a project virtualenv at .venv so nothing is installed into your
# system Python. If you would rather use an environment you already have,
# activate it first and pass --no-venv.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

USE_VENV=1
[[ "${1:-}" == "--no-venv" ]] && USE_VENV=0

PACKAGES=(fastapi "uvicorn[standard]" pydantic pydantic-settings python-multipart
          numpy networkx reportlab jinja2 pytest httpx)

echo "==> Checking Python"
PY="$(command -v python3 || command -v python || true)"
if [[ -z "$PY" ]]; then
  echo "    Python 3.11+ is required but was not found on PATH." >&2
  exit 1
fi
"$PY" - <<'PYEOF'
import sys
if sys.version_info < (3, 11):
    sys.exit(f"    Python 3.11+ required, found {sys.version.split()[0]}")
print(f"    using {sys.version.split()[0]}")
PYEOF

if [[ "$USE_VENV" == "1" ]]; then
  echo "==> Creating virtualenv at .venv"
  "$PY" -m venv .venv 2>/dev/null || {
    echo "    venv creation failed. On Debian/Ubuntu: sudo apt install python3-venv" >&2
    echo "    Or re-run as: scripts/setup.sh --no-venv" >&2
    exit 1
  }
  if [[ -x ".venv/bin/python" ]]; then PY="$ROOT/.venv/bin/python"; else PY="$ROOT/.venv/Scripts/python.exe"; fi
fi

echo "==> Installing Python dependencies"
"$PY" -m pip install --upgrade pip -q
if ! "$PY" -m pip install -q "${PACKAGES[@]}" 2>/dev/null; then
  # Debian and Ubuntu mark the system Python as externally managed. Retry with
  # the flag they expect rather than telling the user to work it out.
  echo "    retrying with --break-system-packages (externally managed environment)"
  "$PY" -m pip install -q --break-system-packages "${PACKAGES[@]}"
fi

echo "==> Building the console"
if command -v npm >/dev/null 2>&1; then
  (cd frontend && npm install --silent && npm run build >/dev/null)
  echo "    console built to frontend/dist"
else
  echo "    npm not found - skipping. The API still runs; only the web console is unavailable."
fi

echo
echo "==> Done."
echo "    Next:  scripts/seed.sh      (generate the synthetic dataset)"
echo "    Then:  scripts/run.sh       (start the platform)"
