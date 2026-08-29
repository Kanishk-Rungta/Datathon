# Run the test suite.
#
#   scripts\test.ps1                     # everything
#   scripts\test.ps1 -m "not slow"       # unit tests only, seconds
#
# Deliberately no param() block: PowerShell binds a leading `-m` to a script
# parameter and fails before pytest ever sees it, even with
# ValueFromRemainingArguments. With no declared parameters every token lands in
# $args untouched, which is the only way pytest's own flags pass through.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot '_python.ps1')
& (Resolve-BootstrapPython) (Join-Path $Root 'cip.py') test @args
exit $LASTEXITCODE
