$ErrorActionPreference = "Stop"

# Microsoft GraphRAG runs in its own virtual environment, .venv-graphrag, apart from the app's .venv: graphrag 3.2.0
# requires Python >=3.11,<3.14, while .venv runs Python 3.14. This script creates .venv-graphrag with Python 3.13 (through
# the py launcher) if it does not exist, then installs requirements-graphrag.txt into it. It never touches .venv.

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectRoot ".venv-graphrag"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements-graphrag.txt"

if (-not (Test-Path $Python)) {
    Write-Host "Creating $VenvDir with Python 3.13 ..."
    & py -3.13 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create $VenvDir. Python 3.13 must be installed (check with: py -0p)."
    }
}

& $Python -m pip install -r $Requirements
