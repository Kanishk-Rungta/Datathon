# Generate the synthetic dataset and build all derived intelligence.
#   scripts\seed.ps1 [-Cases 4200] [-Months 30] [-Reset]
# No param() block: see test.ps1 for why. Arguments are translated to cip.py's.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot '_python.ps1')

$cases = 4200; $months = 30; $reset = $false
for ($i = 0; $i -lt $args.Count; $i++) {
    switch -Regex ($args[$i]) {
        '^-+Cases$'  { $cases  = [int]$args[++$i] }
        '^-+Months$' { $months = [int]$args[++$i] }
        '^-+Reset$'  { $reset  = $true }
        default      { throw "Unknown argument '$($args[$i])'. Use -Cases, -Months or -Reset." }
    }
}

$cipArgs = @('seed', '--cases', $cases, '--months', $months)
if ($reset) { $cipArgs += '--reset' }
& (Resolve-BootstrapPython) (Join-Path $Root 'cip.py') @cipArgs
exit $LASTEXITCODE
