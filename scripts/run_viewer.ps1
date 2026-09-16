$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Viewer = Join-Path $ProjectRoot "viewer\app.py"

if (-not (Test-Path $Python)) {
    throw "Project virtual environment not found: $Python"
}

& $Python $Viewer
