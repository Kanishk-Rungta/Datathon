# Hot-reload development: API on :8000, Vite console on :5173 proxying to it.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot '_python.ps1')
& (Resolve-BootstrapPython) (Join-Path $Root 'cip.py') dev @args
exit $LASTEXITCODE
