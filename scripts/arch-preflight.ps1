$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
git status --short
if (Test-Path '.sentrux/baseline.json') { throw 'Baseline already exists; preserve it for the final regression gate.' }
sentrux gate --save .
if ($LASTEXITCODE -ne 0) { throw 'Sentrux baseline failed' }
if (Test-Path '.sentrux/rules.toml') {
    sentrux check .
    if ($LASTEXITCODE -ne 0) { throw 'Sentrux rules failed' }
} else { Write-Output 'No Sentrux rules configured.' }
