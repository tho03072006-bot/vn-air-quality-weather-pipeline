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
# Forward slashes, because this also runs on a Linux CI runner under pwsh. Windows
# accepts them everywhere; Linux does not accept the backslash form, and a path
# joined as 'airflow\docker-compose.yml' there becomes a single filename that no
# amount of docker-compose flags can rescue.
$Compose = Join-Path $ProjectRoot 'airflow/docker-compose.yml'

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
        Write-Host "  docker compose -f airflow/docker-compose.yml up -d" -ForegroundColor Yellow
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

    # ---- 2b. Wait for the DAG to be idle before triggering.
    #
    # Unpausing a six-hourly DAG on a metadata database that has never seen it makes
    # the scheduler create the current interval's run immediately. That run takes the
    # single slot in the warehouse_writer pool, and warehouse_writer having exactly
    # one slot is a correctness invariant (finding D), not a tunable. A run triggered
    # on top of it therefore sits in 'queued' until the first one finishes.
    #
    # Measured on a GitHub runner before this wait existed: the triggered run stayed
    # queued for the full twenty minutes while a scheduled run executed beside it, and
    # the gate reported that the DAG had not executed. It had. That is a check
    # reporting the wrong cause, which is worse than a check that simply fails, so the
    # collision is now waited out rather than misread. A developer's machine never
    # showed it because a DAG that has been running for days has no missed interval to
    # backfill.
    $idleDeadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ((Get-Date) -lt $idleDeadline) {
        $existing = ConvertFrom-AirflowJson (Invoke-Airflow @('dags', 'list-runs', $DagId, '--output', 'json'))
        $busy = @($existing | Where-Object { $_.state -eq 'queued' -or $_.state -eq 'running' })
        if ($busy.Count -eq 0) { break }
        Write-Host ("  waiting for {0} in-flight run(s) to finish: {1}" -f
            $busy.Count, (($busy | ForEach-Object { "$($_.run_id)=$($_.state)" }) -join ', ')) -ForegroundColor DarkGray
        Start-Sleep -Seconds $PollSeconds
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
        # Say what else was happening before blaming the execution API. A run stuck at
        # 'queued' while another run holds the single warehouse_writer slot is a
        # scheduler doing its job, and reporting that as a scheduling failure sends
        # the reader hunting for a fault that is not there.
        $others = ConvertFrom-AirflowJson (Invoke-Airflow @('dags', 'list-runs', $DagId, '--output', 'json'))
        $blocking = @($others | Where-Object { $_.run_id -ne $runId -and ($_.state -eq 'queued' -or $_.state -eq 'running') })
        if ($blocking.Count -gt 0) {
            Write-Host "" -ForegroundColor Red
            Write-Host "Another run was in flight and holds the single warehouse_writer slot:" -ForegroundColor Red
            $blocking | ForEach-Object { Write-Host "  $($_.run_id) = $($_.state)" -ForegroundColor Red }
            Write-Host "The scheduler was working; this run was waiting for the pool." -ForegroundColor Red
        }
    }
    Write-Host "Task states:" -ForegroundColor Red
    Invoke-Airflow @('tasks', 'states-for-dag-run', $DagId, $runId) | Out-String | Write-Host
    # An empty task log is itself the signature of the execution-API failure, so
    # point at the directory rather than assuming a traceback exists to print.
    $logDir = Join-Path $ProjectRoot ("airflow/logs/dag_id={0}/run_id={1}" -f $DagId, $runId)
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
