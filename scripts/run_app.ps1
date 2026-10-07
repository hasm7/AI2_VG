$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Backend = Join-Path $ProjectRoot "backend\app.py"
$Frontend = Join-Path $ProjectRoot "frontend"
# Microsoft GraphRAG runs in its own environment. It is optional: without it the rest of the app starts as usual.
$GraphRagPython = Join-Path $ProjectRoot ".venv-graphrag\Scripts\python.exe"
$GraphRagService = Join-Path $ProjectRoot "graphrag_service\server.py"

if (-not (Test-Path $Python)) {
    throw "Project virtual environment not found: $Python"
}

if (-not (Test-Path $Backend)) {
    throw "Backend app not found: $Backend"
}

if (-not (Test-Path (Join-Path $Frontend "package.json"))) {
    throw "Frontend package.json not found: $Frontend"
}

function Wait-ForBackend {
    param(
        [string] $Url,
        [int] $TimeoutSeconds = 20
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500) {
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 400
        }
    }

    throw "Backend did not become ready at $Url within $TimeoutSeconds seconds."
}

$BackendProcess = $null
$GraphRagProcess = $null

try {
    if (Test-Path $GraphRagPython) {
        try {
            Write-Host "Starting Microsoft GraphRAG service on http://127.0.0.1:8100 ..."
            $GraphRagProcess = Start-Process `
                -FilePath $GraphRagPython `
                -ArgumentList @($GraphRagService) `
                -WorkingDirectory $ProjectRoot `
                -PassThru `
                -WindowStyle Hidden
        }
        catch {
            Write-Host "Microsoft GraphRAG service not started: $($_.Exception.Message)"
        }
    }
    else {
        Write-Host "Microsoft GraphRAG service skipped: $GraphRagPython not found (.\scripts\install_graphrag_deps.ps1)."
    }

    Write-Host "Starting backend on http://127.0.0.1:8000 ..."
    $BackendProcess = Start-Process `
        -FilePath $Python `
        -ArgumentList @($Backend) `
        -WorkingDirectory $ProjectRoot `
        -PassThru `
        -WindowStyle Hidden

    Wait-ForBackend -Url "http://127.0.0.1:8000/api/neo4j/status"

    Write-Host "Backend is ready."
    Write-Host "Starting React frontend on http://127.0.0.1:5173 ..."
    Push-Location $Frontend
    & npm.cmd run dev -- --host 127.0.0.1
}
finally {
    Pop-Location -ErrorAction SilentlyContinue

    if ($BackendProcess -and -not $BackendProcess.HasExited) {
        Write-Host "Stopping backend ..."
        Stop-Process -Id $BackendProcess.Id -Force
    }

    if ($GraphRagProcess -and -not $GraphRagProcess.HasExited) {
        Write-Host "Stopping Microsoft GraphRAG service ..."
        # The venv's python.exe starts the real interpreter as a child process, which holds the port: stop the tree.
        & taskkill.exe /T /F /PID $GraphRagProcess.Id | Out-Null
    }
}
