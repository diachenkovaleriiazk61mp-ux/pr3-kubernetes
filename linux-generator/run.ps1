$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
if (!(Test-Path (Join-Path $project 'results/seed.json'))) {
    throw 'Put linux-generator inside pr3-kubernetes, beside results, then run again.'
}
& docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop first.' }
$hpaText = & kubectl --context kind-pr3 get hpa web --output=json
if ($LASTEXITCODE -ne 0) { throw 'Could not check the current HPA.' }
$hpa = $hpaText | ConvertFrom-Json
if ($hpa.spec.minReplicas -ne 1 -or $hpa.spec.maxReplicas -ne 1) {
    throw 'Diagnostic needs HPA minReplicas=1 and maxReplicas=1.'
}
$deploymentText = & kubectl --context kind-pr3 get deployment web --output=json
if ($LASTEXITCODE -ne 0) { throw 'Could not check web.' }
$deployment = $deploymentText | ConvertFrom-Json
if ($deployment.spec.replicas -ne 1 -or $deployment.status.readyReplicas -ne 1) {
    throw 'Wait until one web replica is ready.'
}
Write-Host 'Building the Linux generator. The first build downloads Python and aiohttp.'
& docker build --tag pr3-generator-probe:local $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Generator image build failed.' }
Write-Host 'Running two 30-second diagnostic tests; existing lab results will be preserved.'
& docker run --rm --network kind --mount "type=bind,source=$project,target=/data" pr3-generator-probe:local
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic did not finish successfully. See the output above.' }
