# Shared interpreter resolution, sourced by the other bash scripts.
#
# These scripts only need an interpreter good enough to *start* cip.py, which
# is stdlib-only; cip.py itself then finds or builds the virtualenv. The
# project venv is still preferred when it exists, so a second interpreter
# never sneaks into a working setup.
#
# Every candidate is executed before it is accepted. On Windows/Git Bash
# `command -v python3` finds the Microsoft Store App Execution Alias: on PATH,
# not an interpreter, prints an advert instead of running. Being on PATH and
# being usable are different things.

resolve_python() {
  local root="$1"
  if [[ -x "$root/.venv/bin/python" ]]; then
    echo "$root/.venv/bin/python"
    return 0
  elif [[ -x "$root/.venv/Scripts/python.exe" ]]; then   # Windows / Git Bash
    echo "$root/.venv/Scripts/python.exe"
    return 0
  elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
    echo "$VIRTUAL_ENV/bin/python"
    return 0
  fi

  local candidate path
  for candidate in python3 python py; do
    path="$(command -v "$candidate" 2>/dev/null || true)"
    [[ -z "$path" ]] && continue
    if "$path" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      echo "$path"
      return 0
    fi
  done

  echo "Python 3.11+ was not found on PATH. See https://www.python.org/downloads/" >&2
  return 1
}
