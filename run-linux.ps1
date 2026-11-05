param([switch]$ProfilesOnly, [int]$MaxRate = 2400)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (Test-Path .\tools\kubectl.exe) {
    $env:Path = "$(Join-Path $PSScriptRoot 'tools');$env:Path"
}
if (!(Test-Path .\.venv\Scripts\python.exe)) { throw 'The project Python environment is missing.' }
& docker image inspect pr3-generator-probe:local --format '{{.Id}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'The Linux probe image is missing; finish the probe setup first.' }
& kubectl --context kind-pr3 get nodes
if ($LASTEXITCODE -ne 0) { throw 'The pr3 cluster is unavailable.' }
if (!$ProfilesOnly) {
    $items = @('results', 'figures', 'RESULTS.md') | Where-Object { Test-Path $_ }
    if ($items.Count -gt 0) {
        $backup = Join-Path $PSScriptRoot ('previous-runs\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
        New-Item -ItemType Directory -Path $backup | Out-Null
        foreach ($item in $items) { Move-Item -LiteralPath $item -Destination $backup }
        Write-Host "Previous measurements saved in $backup"
    }
}
$env:PR3_LOAD_BACKEND = 'linux'
$stage = '--capacity-only'
if ($ProfilesOnly) { $stage = '--profiles-only' }
Write-Host "Linux HTTP generator, published port 8080, stage: $stage"
& .\.venv\Scripts\python.exe -u .\experiment.py $stage --max-rate $MaxRate
if ($LASTEXITCODE -ne 0) { throw 'Measurement stage stopped. Keep results and share the error output.' }
