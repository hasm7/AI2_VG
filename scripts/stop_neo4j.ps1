$ErrorActionPreference = "Stop"

# Stops the Neo4j database started with .\scripts\run_neo4j.ps1 (not one started by Neo4j Desktop; stop that one in
# Desktop). It first asks the process tree to close and waits for the server to shut down; only if it has not stopped
# after 30 seconds is it ended by force. Neo4j recovers after a forced stop on its next start, from its transaction
# log, but a clean stop is quicker.

$Neo4jDesktopRoot = Join-Path $env:USERPROFILE ".Neo4jDesktop2"
$DbmsHome = if ($env:NEO4J_DBMS_HOME) { $env:NEO4J_DBMS_HOME } else {
    Join-Path $Neo4jDesktopRoot "Data\dbmss\dbms-5bffa807-f4ad-44c1-b173-404843e05d7a"
}
$PidFile = Join-Path $DbmsHome "run\run_neo4j.pid"

if (-not (Test-Path $PidFile)) {
    throw "No database started by run_neo4j.ps1 is recorded ($PidFile)."
}
$wrapperId = [int](Get-Content $PidFile -Raw).Trim()
if (-not (Get-Process -Id $wrapperId -ErrorAction SilentlyContinue)) {
    Remove-Item $PidFile
    Write-Host "Neo4j was not running."
    exit 0
}

$serverRunning = {
    Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($DbmsHome) }
}

Write-Host "Stopping Neo4j ..."
# Without /F: a request to close, which lets the server run its shutdown.
taskkill /PID $wrapperId /T 2>$null | Out-Null
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline -and (& $serverRunning)) {
    Start-Sleep -Milliseconds 500
}
if (& $serverRunning) {
    Write-Host "Neo4j did not stop within 30 seconds; ending it."
    taskkill /PID $wrapperId /T /F 2>$null | Out-Null
    foreach ($process in (& $serverRunning)) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
Remove-Item $PidFile -ErrorAction SilentlyContinue
Write-Host "Neo4j stopped."
