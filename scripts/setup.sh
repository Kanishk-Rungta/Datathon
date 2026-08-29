#!/usr/bin/env bash
# Thin wrapper. The implementation lives in ../cip.py.
#
# There is one implementation on purpose: this script, its PowerShell twin and
# cip.py used to be three separate copies of "make a venv, install these
# packages, build the console", which is three chances to install a different
# set. Everything here does is find a usable Python and hand over.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/_python.sh"
exec "$(resolve_python "$ROOT")" "$ROOT/cip.py" setup "$@"
