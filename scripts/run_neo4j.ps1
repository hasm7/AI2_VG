$ErrorActionPreference = "Stop"

# Starts the project's Neo4j database without the Neo4j Desktop app, hidden in the background. It runs the database
# Desktop installed, from its own folder: the same data, the same ports (Bolt 7687, HTTP 7474) and the same password,
# so the backend connects as before. Only Desktop's window (an Electron app) is left out.
#
# Stop the database in Neo4j Desktop first: two servers must never run on the same data folder.
# Stop this one with .\scripts\stop_neo4j.ps1.
#
# It starts the database as Desktop does: JAVA_HOME set to the Java runtime Desktop ships, the Enterprise license
# accepted with NEO4J_ACCEPT_LICENSE_AGREEMENT=yes (as Desktop does on every start; free for development on this
# machine), and `bin\neo4j.ps1 console` run hidden with its output appended to a log file.

$Neo4jDesktopRoot = Join-Path $env:USERPROFILE ".Neo4jDesktop2"
# The DBMS folder can be overridden with NEO4J_DBMS_HOME; by default the project's database in Neo4j Desktop.
$DbmsHome = if ($env:NEO4J_DBMS_HOME) { $env:NEO4J_DBMS_HOME } else {
    Join-Path $Neo4jDesktopRoot "Data\dbmss\dbms-5bffa807-f4ad-44c1-b173-404843e05d7a"
}
$JavaHome = Join-Path $Neo4jDesktopRoot "Cache\runtime\zulu21.50.19-ca-jre21.0.11-win_x64"
$Neo4jScript = Join-Path $DbmsHome "bin\neo4j.ps1"
$LogFile = Join-Path $DbmsHome "logs\neo4j-console.log"
# Neo4j writes its own log here; the console log above only catches output from before it starts logging.
$Neo4jLog = Join-Path $DbmsHome "logs\neo4j.log"
$PidFile = Join-Path $DbmsHome "run\run_neo4j.pid"

if (-not (Test-Path $Neo4jScript)) {
    throw "Neo4j database not found: $Neo4jScript"
}
if (-not (Test-Path (Join-Path $JavaHome "bin\java.exe"))) {
    throw "Java runtime not found: $JavaHome"
}

# Never a second server on the same data: stop if this database or anything on its ports is already running.
$running = Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($DbmsHome) }
if ($running) {
    throw "This Neo4j database is already running (java PID $($running.ProcessId -join ', ')). " +
        "Stop it in Neo4j Desktop, or with .\scripts\stop_neo4j.ps1."
}
if (Get-NetTCPConnection -State Listen -LocalPort 7687 -ErrorAction SilentlyContinue) {
    throw "Something already listens on port 7687 (Bolt). Stop the other Neo4j first."
}

$env:JAVA_HOME = $JavaHome
$env:NEO4J_ACCEPT_LICENSE_AGREEMENT = "yes"

New-Item -ItemType Directory -Force (Split-Path -Parent $PidFile) | Out-Null
$command = "& '$Neo4jScript' console *>> '$LogFile'"
$wrapper = Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -PassThru `
    -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", $command)
Set-Content -Path $PidFile -Value $wrapper.Id -Encoding ascii

Write-Host "Starting Neo4j (log: $Neo4jLog) ..."
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if (Get-NetTCPConnection -State Listen -LocalPort 7687 -ErrorAction SilentlyContinue) {
        Write-Host "Neo4j is ready: bolt://localhost:7687, browser http://localhost:7474"
        exit 0
    }
    if ($wrapper.HasExited) {
        throw "Neo4j stopped while starting. See the end of $Neo4jLog and $LogFile"
    }
    Start-Sleep -Milliseconds 500
}
throw "Neo4j did not open port 7687 within 90 seconds. See the end of $Neo4jLog and $LogFile"
