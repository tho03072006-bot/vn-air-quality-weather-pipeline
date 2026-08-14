<#
.SYNOPSIS
    Proves Airflow can actually execute a task on the scheduler path.

.DESCRIPTION
    `airflow dags test` runs every task in-process. It never goes near the
    executor and never calls the Task Execution API, so it passes even when
    scheduled execution is completely broken -- which is exactly what happened
    here: a missing AIRFLOW__CORE__EXECUTION_API_SERVER_URL left every scheduled
    task dying with an empty log while `dags test` reported 4/4 success. See
    finding M in docs/code-audit-and-risk-register.md.

    `airflow dags trigger` creates a real DagRun that the scheduler picks up and
    the executor runs. That is the path this gate drives.

    Like the browser gates this needs a running service, so it is deliberately
    kept out of scripts/verify.ps1, which is contractually offline.

    Side effect, stated because a gate should not surprise its operator: the DAG
    is unpaused for the duration and its original paused state is restored at the
    end. The forecast DAG is used because it needs no API key and its mart is
    idempotent, so an extra run changes no serving row.

.EXAMPLE
    docker compose -f airflow\docker-compose.yml up -d
    .\scripts\verify_airflow_scheduling.ps1

.EXAMPLE
    .\scripts\verify_airflow_scheduling.ps1 -DagId vn_air_quality_weather_daily -TimeoutMinutes 30
#>
[CmdletBinding()]
param(
    [string]$DagId = 'vn_air_quality_weather_forecast',
    [int]$TimeoutMinutes = 15,
    [int]$PollSeconds = 10
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Compose = Join-Path $ProjectRoot 'airflow\docker-compose.yml'

function Invoke-Airflow {
    <#
        Runs an Airflow CLI command inside the scheduler container. Returns the
        raw stdout lines; the caller checks $LASTEXITCODE when it matters.
    #>
    param([Parameter(Mandatory)][string[]]$Arguments)

    & docker compose -f $Compose exec -T airflow-scheduler airflow @Arguments
}

function ConvertFrom-AirflowJson {
    <#
        The Airflow CLI prints structured log lines to stdout before the payload,
        so `--output json` output is not parseable as-is. Take the first line that
        actually starts a JSON document and ignore the logging around it.
    #>
    param([string[]]$Lines)

    $payload = $Lines | Where-Object { $_.TrimStart().StartsWith('[') -or $_.TrimStart().StartsWith('{') } |
        Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($payload)) { return $null }
    return $payload | ConvertFrom-Json
}

Push-Location $ProjectRoot
$originalPaused = $null

try {
    # ---- 1. The stack has to be up. Exit 2, not 1: an absent service is an
    # ---- operator condition, not a failing assertion about the code.
    $running = & docker compose -f $Compose ps --status running --format '{{.Service}}'
    if ($LASTEXITCODE -ne 0 -or -not ($running -match 'airflow-scheduler')) {
        Write-Host "Airflow is not running. Start it first:" -ForegroundColor Yellow
        Write-Host "  docker compose -f airflow\docker-compose.yml up -d" -ForegroundColor Yellow
        exit 2
    }

    # ---- 2. Record the paused state so the gate leaves the DAG as it found it.
    $details = ConvertFrom-AirflowJson (Invoke-Airflow @('dags', 'details', $DagId, '--output', 'json'))
    if ($null -eq $details) {
        Write-Host "Could not read details for $DagId." -ForegroundColor Red
        exit 1
    }
    # The CLI renders booleans as the strings "True"/"False", so a [bool] cast
    # would make every non-empty value true and the gate would always re-pause.
    $originalPaused = ([string]$details[0].is_paused -eq 'True')
    Write-Host "$DagId is_paused=$originalPaused before the run" -ForegroundColor Cyan

    if ($originalPaused) {
        Invoke-Airflow @('dags', 'unpause', $DagId) | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not unpause $DagId" }
    }

    # ---- 3. Trigger a run with an id we chose, so polling cannot match somebody
    # ---- else's run and report a stale success.
    $runId = "verify_scheduling__{0}" -f ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
    Write-Host "Triggering $DagId run_id=$runId" -ForegroundColor Cyan
    Invoke-Airflow @('dags', 'trigger', $DagId, '--run-id', $runId) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not trigger $DagId" }

    # ---- 4. Wait for a terminal state.
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $state = 'queued'
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds $PollSeconds
        $runs = ConvertFrom-AirflowJson (Invoke-Airflow @('dags', 'list-runs', $DagId, '--output', 'json'))
        if ($null -eq $runs) { continue }
        $run = $runs | Where-Object { $_.run_id -eq $runId }
        if ($null -eq $run) { continue }
        $state = [string]$run.state
        Write-Host "  state=$state" -ForegroundColor DarkGray
        if ($state -eq 'success' -or $state -eq 'failed') { break }
    }

    # ---- 5. Report.
    if ($state -eq 'success') {
        $states = Invoke-Airflow @('tasks', 'states-for-dag-run', $DagId, $runId) | Out-String
        Write-Host $states
        Write-Host "PASS: $DagId executed on the scheduler path (run_id=$runId)" -ForegroundColor Green
        exit 0
    }

    Write-Host ""
    if ($state -eq 'failed') {
        Write-Host "FAIL: $DagId run $runId failed." -ForegroundColor Red
    }
    else {
        # A task that dies on the execution API goes up_for_retry rather than
        # failed, so the run sits in 'running' until its retries are exhausted.
        # Reaching the deadline in a non-terminal state is a failure of this gate,
        # not an inconclusive result -- the DAG did not execute.
        Write-Host "FAIL: $DagId run $runId was still '$state' after $TimeoutMinutes minute(s)." -ForegroundColor Red
    }
    Write-Host "Task states:" -ForegroundColor Red
    Invoke-Airflow @('tasks', 'states-for-dag-run', $DagId, $runId) | Out-String | Write-Host
    # An empty task log is itself the signature of the execution-API failure, so
    # point at the directory rather than assuming a traceback exists to print.
    $logDir = Join-Path $ProjectRoot ("airflow\logs\dag_id={0}\run_id={1}" -f $DagId, $runId)
    Write-Host "Task logs: $logDir" -ForegroundColor Red
    Write-Host "An empty log there means the task process died before it could write one," -ForegroundColor Red
    Write-Host "which is what a bad AIRFLOW__CORE__EXECUTION_API_SERVER_URL looks like." -ForegroundColor Red
    exit 1
}
catch {
    Write-Host ""
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
finally {
    if ($null -ne $originalPaused -and $originalPaused) {
        Write-Host "Restoring $DagId to paused" -ForegroundColor DarkGray
        & docker compose -f $Compose exec -T airflow-scheduler airflow dags pause $DagId | Out-Null
    }
    Pop-Location
}
