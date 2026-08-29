# Shared interpreter resolution, dot-sourced by the other PowerShell scripts.
#
# These scripts only need an interpreter good enough to *start* cip.py, which
# is stdlib-only; cip.py itself then finds or builds the virtualenv. So this
# looks for any usable Python, not the project one.
#
# `python3` is deliberately not tried: on a stock Windows install it resolves
# to the Microsoft Store App Execution Alias, a stub that is on PATH, is not an
# interpreter, and prints an advert instead of running. `py` is the official
# launcher and is preferred when present.
function Resolve-BootstrapPython {
    foreach ($candidate in @('py', 'python')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        & $found.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { return $found.Source }
    }
    throw @'
Python 3.11+ was not found.
Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
On Windows, `python3` may be the Microsoft Store alias rather than a real install.
'@
}
