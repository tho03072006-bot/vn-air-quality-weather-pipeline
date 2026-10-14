<#
.SYNOPSIS
    Check, without changing anything, that this machine can run the pipeline end to end.

.DESCRIPTION
    Every check here is one that has already cost time on this machine by failing in
    a way that named the wrong cause. The script only reads: it starts no service,
    edits no setting and opens the warehouse read-only. Each failing check prints the
    command that fixes it, so the output doubles as the bootstrap procedure.

        PASS  the thing is in the state the next step needs
        WARN  it works, but something is stale or will surprise you
        FAIL  the next step will fail; the remedy is printed beside it

    Exit code is 0 when nothing FAILs and 1 otherwise.

    What it checks, and why each one is here:

      * Docker CLI. Docker Desktop is installed per-user and its bin directory was
        never added to PATH, so `docker` is "not recognised" while the engine runs
        fine. The bin directory also holds docker-credential-desktop, without which a
        pull fails with a credentials error that does not mention PATH.
      * Which SQL Server owns port 1433. Three instances here are all statically
        configured for 1433 and only one can bind it. vn_air_quality is on SQLEXPRESS;
        any other owner answers the login and then reports the database missing.
      * The publish credential in this shell. It lives in user-scope environment
        variables, not in .env, and compose passes it into the containers from here.
        A shell opened before the variables were set has none of them.
      * Ports. Tomcat holds 8080; Airflow's API server is published on 8081 so both
        halves of the project can run together.
      * The forecast horizon. Pages read forecast hours that are still ahead; once a
        warehouse's newest vintage has fully elapsed, every page is empty and correct.

.PARAMETER AirflowPort
    Host port the Airflow API server is published on. Matches AIRFLOW_API_PORT in
    airflow/docker-compose.yml.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\preflight.ps1
#>
[CmdletBinding()]
param(
    [int] $AirflowPort = $(if ($env:AIRFLOW_API_PORT) { [int]$env:AIRFLOW_API_PORT } else { 8081 }),
    [int] $TomcatPort = 8080
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$failures = 0

function Report {
    param([ValidateSet('PASS', 'WARN', 'FAIL', 'INFO')] [string] $Level, [string] $Check, [string] $Detail, [string] $Fix)
    $colour = @{ PASS = 'Green'; WARN = 'Yellow'; FAIL = 'Red'; INFO = 'Gray' }[$Level]
    Write-Host ("{0}  {1,-26} {2}" -f $Level, $Check, $Detail) -ForegroundColor $colour
    if ($Fix) { Write-Host ("      fix: {0}" -f $Fix) -ForegroundColor DarkGray }
    if ($Level -eq 'FAIL') { $script:failures++ }
}

function Get-PortOwner {
    param([int] $Port)
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $null }
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    [pscustomobject]@{ Pid = $listener.OwningProcess; Name = $process.ProcessName }
}

Write-Host "Preflight for $repo" -ForegroundColor Cyan

# ---- Python -------------------------------------------------------------------
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (Test-Path $python) {
    $version = (& $python -c 'import sys; print(sys.version.split()[0])').Trim()
    if ($version -like '3.11.*') { Report PASS 'python venv' $version }
    else { Report FAIL 'python venv' "Python $version; pyproject requires 3.11" 'py -3.11 -m venv .venv' }
}
else {
    Report FAIL 'python venv' 'no .venv' 'py -3.11 -m venv .venv; .\.venv\Scripts\python -m pip install -e ".[dev,etl]"'
}

# ---- Docker -------------------------------------------------------------------
$dockerBin = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Report PASS 'docker on PATH' (Get-Command docker).Source
}
elseif (Test-Path (Join-Path $dockerBin 'docker.exe')) {
    # Prepended for the rest of this script only, so the checks below can run.
    $env:PATH = "$dockerBin;$env:PATH"
    Report FAIL 'docker on PATH' "installed at $dockerBin but not on PATH" ('[Environment]::SetEnvironmentVariable(''PATH'', [Environment]::GetEnvironmentVariable(''PATH'',''User'') + '';' + $dockerBin + ''', ''User'')  # then open a new shell')
}
else {
    Report FAIL 'docker on PATH' 'Docker Desktop not found' 'install Docker Desktop'
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    $server = & docker version --format '{{.Server.Version}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $server) { Report PASS 'docker engine' "server $server" }
    else { Report FAIL 'docker engine' 'not responding' 'start Docker Desktop' }

    $compose = & docker compose version --short 2>$null
    if ($LASTEXITCODE -eq 0) { Report PASS 'docker compose' $compose }
    else { Report FAIL 'docker compose' 'plugin missing' 'reinstall Docker Desktop' }
}

# ---- SQL Server ---------------------------------------------------------------
$owner = Get-PortOwner 1433
if (-not $owner) {
    Report FAIL 'sql server :1433' 'nothing listening' 'Start-Service ''MSSQL$SQLEXPRESS''   # elevated; do not start MSSQLSERVER or SQLEXPRESS01, they share port 1433'
}
else {
    $service = Get-CimInstance Win32_Service -Filter "ProcessId=$($owner.Pid)" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($service.Name -eq 'MSSQL$SQLEXPRESS') {
        Report PASS 'sql server :1433' 'owned by MSSQL$SQLEXPRESS'
        $probe = & sqlcmd -S tcp:localhost,1433 -E -C -b -h-1 -W -Q "SET NOCOUNT ON; SELECT CASE WHEN DB_ID('vn_air_quality') IS NULL THEN 'no-db' ELSE 'db' END + ',' + CAST((SELECT COUNT(*) FROM sys.server_principals WHERE name IN ('vn_air_web','vn_air_publish')) AS varchar(2));" 2>&1
        $parts = "$probe".Trim() -split ','
        if ($parts[0] -ne 'db') { Report FAIL 'serving database' 'vn_air_quality is absent' 'powershell -File scripts\create_serving_logins.ps1 -Persist' }
        elseif ($parts[1] -ne '2') { Report FAIL 'serving logins' "found $($parts[1]) of 2" 'powershell -File scripts\create_serving_logins.ps1 -Persist' }
        else { Report PASS 'serving database' 'vn_air_quality, vn_air_web, vn_air_publish' }
    }
    else {
        Report FAIL 'sql server :1433' "owned by $($service.Name), which does not hold vn_air_quality" "stop $($service.Name) only if nothing else on this machine needs it, then Start-Service 'MSSQL`$SQLEXPRESS'"
    }
}

# ---- Credentials (presence only; values are never read into output) ------------
$userInShell = $env:SQLSERVER_USER
if ($userInShell -eq 'vn_air_publish' -and $env:SQLSERVER_PASSWORD) {
    Report PASS 'publish credential' 'SQLSERVER_USER=vn_air_publish, password present in this shell'
}
elseif ([Environment]::GetEnvironmentVariable('SQLSERVER_PASSWORD', 'User')) {
    Report FAIL 'publish credential' 'set at user scope but not in this shell' 'open a new shell; this one predates the variables'
}
else {
    Report FAIL 'publish credential' 'SQLSERVER_USER/SQLSERVER_PASSWORD are not set' 'powershell -File scripts\create_serving_logins.ps1 -Persist'
}
if ($env:VN_AIR_WEB_PASSWORD -or [Environment]::GetEnvironmentVariable('VN_AIR_WEB_PASSWORD', 'User')) {
    Report PASS 'web credential' 'VN_AIR_WEB_PASSWORD present (read by Tomcat setenv.bat)'
}
else {
    Report FAIL 'web credential' 'VN_AIR_WEB_PASSWORD is not set' 'powershell -File scripts\create_serving_logins.ps1 -Persist'
}

# ---- Ports --------------------------------------------------------------------
$tomcat = Get-PortOwner $TomcatPort
if (-not $tomcat) { Report PASS "port $TomcatPort (Tomcat)" 'free' }
elseif ($tomcat.Name -eq 'java') { Report PASS "port $TomcatPort (Tomcat)" "held by java pid $($tomcat.Pid)" }
else { Report FAIL "port $TomcatPort (Tomcat)" "held by $($tomcat.Name) pid $($tomcat.Pid)" 'an old Airflow stack still publishes 8080: docker compose -f airflow/docker-compose.yml up -d recreates it on 8081' }

$airflow = Get-PortOwner $AirflowPort
if (-not $airflow) { Report PASS "port $AirflowPort (Airflow)" 'free' }
elseif ($airflow.Name -match 'docker|wslrelay|vpnkit') { Report PASS "port $AirflowPort (Airflow)" "published by $($airflow.Name)" }
else { Report FAIL "port $AirflowPort (Airflow)" "held by $($airflow.Name) pid $($airflow.Pid)" 'set AIRFLOW_API_PORT to a free port before docker compose up' }

# ---- Warehouse ----------------------------------------------------------------
$warehouse = Join-Path $repo 'data\warehouse\vn_air_quality_weather.duckdb'
if ((Test-Path $warehouse) -and (Test-Path $python)) {
    # Fed through stdin rather than -c: Windows PowerShell 5.1 strips the inner
    # quotes of a native argument, and SQL needs them.
    $query = @'
import sys, duckdb
con = duckdb.connect(sys.argv[1], read_only=True)
total, ahead, vintage = con.execute(
    "select count(distinct location_key),"
    " count(distinct location_key) filter (where valid_at_utc >= now()),"
    " max(forecast_issued_at_utc)"
    " from analytics.mart_location_hourly_forecast"
).fetchone()
print(f"{total}|{ahead}|{vintage}")
'@
    $ErrorActionPreference = 'Continue'
    $out = @($query | & $python - $warehouse 2>&1 | ForEach-Object { "$_" })
    $ErrorActionPreference = 'Stop'
    $last = $out | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) {
        Report WARN 'forecast horizon' "could not read the warehouse ($last); a running DAG may hold it"
    }
    else {
        $total, $ahead, $vintage = $last -split '\|'
        if ([int]$ahead -eq 0) { Report WARN 'forecast horizon' "0/$total anchors have an hour ahead; newest vintage $vintage" 'trigger vn_air_quality_weather_forecast (scripts\verify_airflow_scheduling.ps1)' }
        elseif ([int]$ahead -lt [int]$total) { Report WARN 'forecast horizon' "$ahead/$total anchors have an hour ahead; newest vintage $vintage" }
        else { Report PASS 'forecast horizon' "$ahead/$total anchors have hours ahead; newest vintage $vintage" }
    }
}
else {
    Report FAIL 'warehouse' "missing at $warehouse" 'python -m vn_air_quality_weather.forecast_pipeline --all-provinces'
}

# ---- Publish timer ------------------------------------------------------------
$task = Get-ScheduledTask -TaskName 'VN Air - publish serving marts' -ErrorAction SilentlyContinue
if (-not $task) {
    Report WARN 'publish timer' 'not registered' 'powershell -File scripts\register_publish_timer.ps1'
}
else {
    $info = $task | Get-ScheduledTaskInfo
    $detail = "state=$($task.State) last=$($info.LastRunTime) result=$($info.LastTaskResult)"
    if ($task.State -eq 'Disabled') { Report WARN 'publish timer' $detail 'Enable-ScheduledTask -TaskName ''VN Air - publish serving marts''   # after the stack is up' }
    elseif ($info.LastTaskResult -ne 0 -and $info.LastRunTime.Year -gt 2000) { Report WARN 'publish timer' $detail 'read logs\publish_to_sqlserver.log' }
    else { Report PASS 'publish timer' $detail }
}

# ---- Airflow stack (informational) --------------------------------------------
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $running = @(& docker compose -f (Join-Path $repo 'airflow/docker-compose.yml') ps --status running --services 2>$null | Where-Object { $_ })
    Report INFO 'airflow services up' $(if ($running.Count) { $running -join ', ' } else { 'none' })
}

Write-Host ''
if ($failures) {
    Write-Host "$failures check(s) failed." -ForegroundColor Red
    exit 1
}
Write-Host 'Nothing blocks the next step.' -ForegroundColor Green
exit 0
