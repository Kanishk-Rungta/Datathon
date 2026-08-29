# Thin wrapper. The implementation lives in ..\cip.py.
#
# There is one implementation on purpose: this script, its bash twin and cip.py
# used to be three separate copies of "make a venv, install these packages,
# build the console", which is three chances to install a different set.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot '_python.ps1')
& (Resolve-BootstrapPython) (Join-Path $Root 'cip.py') setup @args
exit $LASTEXITCODE
