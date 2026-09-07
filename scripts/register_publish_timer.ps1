<#
.SYNOPSIS
Register (or remove) the Windows scheduled task that republishes the serving marts.

.DESCRIPTION
The web application reads SQL Server, and five of the fifteen relations it reads are
dbt views that evaluate `current_timestamp` on every query. Copied into static tables
they stop moving, so `mart_current_conditions` keeps recommending an hour that has
already passed and the freshness columns report a snapshot as FRESH. Republishing on
a short cadence keeps the frozen clock inside one interval.

Five minutes is the default because the marts are hourly and the freshness thresholds
are seven and thirteen hours: at that cadence the published clock is never wrong by
enough to change a label. `verify_sqlserver_publish.py` fails when the published clock
is more than fifteen minutes old, which is this interval with room for one missed tick.

Runs as the current user, only while that user is logged on, so no password is stored.
That is the right trade for a development machine; a server would want a service
account and `-LogonType Password`.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_publish_timer.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_publish_timer.ps1 -Remove
#>

[CmdletBinding()]
param(
    [string] $TaskName = 'VN Air - publish serving marts',
    [int] $IntervalMinutes = 5,
    [switch] $Remove
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$tick = Join-Path $repo 'scripts\publish_timer_tick.ps1'

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed the existing task '$TaskName'."
}
if ($Remove) {
    if (-not $existing) { Write-Output "No task named '$TaskName' to remove." }
    exit 0
}

if (-not (Test-Path $tick)) { throw "No tick script at $tick" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$tick`"" `
    -WorkingDirectory $repo

# Two triggers, not one. The repeating trigger carries the cadence; the logon trigger
# is what restarts it after a reboot, because a task that runs only while its user is
# logged on does not resume a repetition that was interrupted by shutdown.
#
# -RepetitionDuration is deliberately omitted. An empty Duration is how the Task
# Scheduler schema spells "forever"; the obvious spelling, [TimeSpan]::MaxValue,
# serialises to P99999999DT23H59M59S and is rejected as out of range.
$repeating = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger @($repeating, $atLogon) `
    -Settings $settings -Principal $principal `
    -Description "Republishes the DuckDB serving marts into SQL Server every $IntervalMinutes minutes. Five of them are dbt views that read current_timestamp, so a static copy goes stale on its own." `
    | Out-Null

Write-Output "Registered '$TaskName': every $IntervalMinutes minutes, and at logon."
Write-Output "Log: $(Join-Path $repo 'logs\publish_to_sqlserver.log')"
Write-Output "Remove it with: powershell -ExecutionPolicy Bypass -File scripts\register_publish_timer.ps1 -Remove"
