$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.local/uv-cache'
$pytestTemp = '.local/pytest-postflight-' + [guid]::NewGuid().ToString('N')
uv run --locked pytest --basetemp=$pytestTemp -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
uv run --locked python -m ai_market_intelligence.cli package-check
if ($LASTEXITCODE -ne 0) { throw 'Package validation failed' }
node --test --test-isolation=none tests/market-stream.test.mjs
if ($LASTEXITCODE -ne 0) { throw 'Protocol regression failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace check failed' }
terraform -chdir=terraform fmt -check -recursive
if ($LASTEXITCODE -ne 0) { throw 'Terraform formatting failed' }
terraform -chdir=terraform validate
if ($LASTEXITCODE -ne 0) { throw 'Terraform validation failed' }
terraform -chdir=terraform test
if ($LASTEXITCODE -ne 0) { throw 'Terraform mocked tests failed' }
uv run --locked python scripts/check_repository.py
if ($LASTEXITCODE -ne 0) { throw 'Repository boundary/secret check failed' }
if (Test-Path '.sentrux/rules.toml') {
    sentrux check .
    if ($LASTEXITCODE -ne 0) { throw 'Sentrux rules failed' }
} else { Write-Output 'No Sentrux rules configured.' }
sentrux gate .
if ($LASTEXITCODE -ne 0) { throw 'Sentrux regression gate failed' }
