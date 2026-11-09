param([switch]$SaveBaseline)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $root
$baseline = Join-Path $root '.sentrux/baseline.json'
if ($SaveBaseline -and (Test-Path -LiteralPath $baseline)) {
    throw 'Preserve the existing Sentrux baseline; this scan cannot replace it.'
}
$index = (Resolve-Path -LiteralPath '.git/index').Path
$indexHash = (Get-FileHash -LiteralPath $index -Algorithm SHA256).Hash
$temporaryIndex = Join-Path $root ('.local/sentrux-' + [guid]::NewGuid().ToString('N') + '.index')
New-Item -ItemType Directory -Force (Split-Path $temporaryIndex -Parent) | Out-Null
Copy-Item -LiteralPath $index -Destination $temporaryIndex
$previousIndex = $env:GIT_INDEX_FILE
try {
    $env:GIT_INDEX_FILE = $temporaryIndex
    git add --intent-to-add -- aidp src scripts tests
    if ($LASTEXITCODE -ne 0) { throw 'Unable to build the complete source inventory.' }
    if (Test-Path -LiteralPath 'node-progress.json') {
        git add --intent-to-add --force -- node-progress.json
        if ($LASTEXITCODE -ne 0) { throw 'Unable to include the local progress inventory.' }
    }
    $sourcePaths = @(git -c core.quotePath=false ls-files --cached)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to record the source inventory.' }
    $scanDirectory = Join-Path $root ('.local/architecture/sentrux-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $scanDirectory | Out-Null
    $inventory = @($sourcePaths | ForEach-Object {
        $sourceFile = Join-Path $root $_
        [ordered]@{
            path = $_
            sha256 = if (Test-Path -LiteralPath $sourceFile -PathType Leaf) {
                (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash.ToLowerInvariant()
            } else { $null }
        }
    })
    [ordered]@{
        schema_version = 1
        recorded_at = [DateTimeOffset]::UtcNow.ToString('o')
        scope = 'Git input inventory; Sentrux may exclude unsupported file types. Not a reconstructed historical baseline.'
        baseline_sha256 = if (Test-Path -LiteralPath $baseline) {
            (Get-FileHash -LiteralPath $baseline -Algorithm SHA256).Hash.ToLowerInvariant()
        } else { $null }
        sources = $inventory
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $scanDirectory 'inventory.json') -Encoding utf8
    Write-Output ('Sentrux input inventory: ' + (Join-Path $scanDirectory 'inventory.json'))
    if ($SaveBaseline) { sentrux gate --save . } else { sentrux gate . }
    $gateExit = $LASTEXITCODE
    if (Test-Path -LiteralPath '.sentrux/rules.toml') {
        sentrux check .
        if ($LASTEXITCODE -ne 0) { $gateExit = $LASTEXITCODE }
    } else { Write-Output 'No Sentrux rules configured.' }
} finally {
    $env:GIT_INDEX_FILE = $previousIndex
    Remove-Item -LiteralPath $temporaryIndex -ErrorAction SilentlyContinue
    if ((Get-FileHash -LiteralPath $index -Algorithm SHA256).Hash -ne $indexHash) {
        throw 'The shared Git index changed during validation; inspect concurrent work.'
    }
}
exit $gateExit
