# Start the platform. Serves the API and the console on one port.
#   scripts\run.ps1 [-Port 8000] [-ServerHost 127.0.0.1]
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot '_python.ps1')

$serverHost = '127.0.0.1'; $port = 8000
for ($i = 0; $i -lt $args.Count; $i++) {
    switch -Regex ($args[$i]) {
        '^-+Port$'       { $port = [int]$args[++$i] }
        '^-+ServerHost$' { $serverHost = [string]$args[++$i] }
        default          { throw "Unknown argument '$($args[$i])'. Use -Port or -ServerHost." }
    }
}

& (Resolve-BootstrapPython) (Join-Path $Root 'cip.py') run --host $serverHost --port $port
exit $LASTEXITCODE
