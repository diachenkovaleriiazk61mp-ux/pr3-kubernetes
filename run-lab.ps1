$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (!(Test-Path .venv/Scripts/python.exe)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python environment' }
}
& .venv/Scripts/python.exe -m pip install -r requirements-lab.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependencies could not be installed' }
& .venv/Scripts/python.exe experiment.py @args
if ($LASTEXITCODE -ne 0) { throw 'Experiment stopped; check results and console output' }
