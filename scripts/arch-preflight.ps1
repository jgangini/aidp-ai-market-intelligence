$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
git status --short
$saveBaseline = -not (Test-Path '.sentrux/baseline.json')
& (Join-Path $PSScriptRoot 'arch-scan.ps1') -SaveBaseline:$saveBaseline
if ($LASTEXITCODE -ne 0) { throw 'Sentrux preflight failed; preserve its evidence and baseline during repair.' }
