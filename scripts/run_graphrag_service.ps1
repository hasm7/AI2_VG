$ErrorActionPreference = "Stop"

# Starts the Microsoft GraphRAG service (port 8100) in its own environment, .venv-graphrag. The backend calls it under
# /api/graphrag/... ; the rest of the app works without it. Install the environment first with
# .\scripts\install_graphrag_deps.ps1.

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv-graphrag\Scripts\python.exe"
$Service = Join-Path $ProjectRoot "graphrag_service\server.py"

if (-not (Test-Path $Python)) {
    throw "GraphRAG virtual environment not found: $Python. Run .\scripts\install_graphrag_deps.ps1 first."
}

& $Python $Service
