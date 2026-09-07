<#
.SYNOPSIS
One tick of the serving publish, with its output appended to a bounded log.

.DESCRIPTION
Invoked by the scheduled task that `register_publish_timer.ps1` creates. It exists
as a file rather than as an inline command in the task definition so the behaviour
is reviewable, testable from a prompt, and changeable without re-registering.

Why a timer publishes at all, when both DAGs already publish at the end of a run:
five of the fifteen serving relations are dbt views that read `current_timestamp`
on every query. `mart_current_conditions` picks the forecast hour nearest to now and
the window models drop hours that have elapsed, so a copy taken six hours ago
describes a "now" that has passed. Copying them on a short cadence keeps the frozen
`as_of_utc` no older than one interval. The alternative -- reimplementing those five
models as T-SQL views -- would put a second copy of the decision logic in a second
language.

The log is trimmed rather than rotated. At roughly 800 bytes a tick and a tick every
five minutes this reaches a few megabytes a week, which is worth bounding and not
worth a rotation scheme.
#>

[CmdletBinding()]
param(
    [int] $KeepLines = 2000
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$script = Join-Path $repo 'scripts\publish_to_sqlserver.py'
$logDirectory = Join-Path $repo 'logs'
$log = Join-Path $logDirectory 'publish_to_sqlserver.log'

if (-not (Test-Path $python)) { throw "No virtualenv interpreter at $python" }
if (-not (Test-Path $logDirectory)) { New-Item -ItemType Directory -Path $logDirectory | Out-Null }

$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')

# The publish prints a fifteen-line table; only the verdict is worth keeping every
# five minutes. A failure prints to stderr and is kept in full, because that is the
# case somebody will read this file to understand.
Push-Location $repo
try {
    $env:PYTHONIOENCODING = 'utf-8'
    $output = & $python $script 2>&1
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($code -eq 0) {
    $verdict = ($output | Where-Object { $_ -match '^Published ' }) -join ' '
    if (-not $verdict) { $verdict = 'published, but the summary line was missing' }
    Add-Content -Path $log -Value "$stamp  OK    $verdict" -Encoding utf8
}
else {
    Add-Content -Path $log -Value "$stamp  FAIL  exit $code" -Encoding utf8
    foreach ($line in $output) {
        Add-Content -Path $log -Value "$stamp        $line" -Encoding utf8
    }
}

# Trim in place, and only when it is worth the read: the file is small enough that
# rewriting it occasionally costs less than any incremental scheme would.
$lines = @(Get-Content -Path $log -Encoding utf8)
if ($lines.Count -gt ($KeepLines * 2)) {
    Set-Content -Path $log -Value ($lines | Select-Object -Last $KeepLines) -Encoding utf8
}

exit $code
