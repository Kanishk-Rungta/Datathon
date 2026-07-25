# Shared interpreter resolution, sourced by the other scripts.
#
# Prefers the project virtualenv if setup.sh created one, then an already
# activated environment, then the system interpreter. Keeping this in one
# place means the scripts cannot disagree about which Python they are using.

resolve_python() {
  local root="$1"
  if [[ -x "$root/.venv/bin/python" ]]; then
    echo "$root/.venv/bin/python"
  elif [[ -x "$root/.venv/Scripts/python.exe" ]]; then   # Windows / Git Bash
    echo "$root/.venv/Scripts/python.exe"
  elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
    echo "$VIRTUAL_ENV/bin/python"
  else
    command -v python3 || command -v python
  fi
}
