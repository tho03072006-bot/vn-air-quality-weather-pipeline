<#
.SYNOPSIS
    Runs every offline quality gate for the pipeline in one command.

.DESCRIPTION
    Ruff format check, Ruff lint, pytest with coverage, compileall for the
    Airflow DAG and dashboard, a deterministic demo warehouse, dbt build, and
    dbt source freshness. No real API call and no AWS call is made, so this is
    safe to run at any time. Stops at the first failing gate and reports which
    one failed.

.EXAMPLE
    .\.venv\Scripts\Activate.ps1
    .\scripts\verify.ps1

.EXAMPLE
    # Reuse the real warehouse instead of the demo fixture.
    .\scripts\verify.ps1 -UseRealWarehouse
#>
[CmdletBinding()]
param(
    [switch]$UseRealWarehouse,
    [switch]$SkipDbt,
    [switch]$SkipFreshness
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Dbt = Join-Path $ProjectRoot '.venv\Scripts\dbt.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found at $Python. Create .venv and install .[dev] first."
}
if (-not $SkipDbt -and -not (Test-Path -LiteralPath $Dbt)) {
    throw "dbt executable not found at $Dbt. Install the project dev dependencies first."
}

Push-Location $ProjectRoot

$failed = @()
$passed = @()

function Invoke-Gate {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Action
    )

    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        $script:failed += $Name
        throw "Gate failed: $Name (exit code $LASTEXITCODE)"
    }
    $script:passed += $Name
    Write-Host "PASS: $Name" -ForegroundColor Green
}

try {
    Invoke-Gate 'ruff format --check' { & $Python -m ruff format --check . }
    Invoke-Gate 'ruff check' { & $Python -m ruff check . }
    Invoke-Gate 'pytest' { & $Python -m pytest }
    Invoke-Gate 'compileall' {
        & $Python -m compileall -q src dashboard airflow/dags scripts
    }

    if (-not $SkipDbt) {
        if ($UseRealWarehouse) {
            $databasePath = Join-Path $ProjectRoot 'data/warehouse/vn_air_quality_weather.duckdb'
            if (-not (Test-Path $databasePath)) {
                throw "Real warehouse not found at $databasePath. Run the pipeline first."
            }
        }
        else {
            $databasePath = Join-Path $ProjectRoot 'data/warehouse/verify.duckdb'
            if (Test-Path $databasePath) { Remove-Item $databasePath -Force }
            Invoke-Gate 'build demo warehouse' {
                & $Python scripts/build_demo_warehouse.py --database $databasePath
            }
        }

        $env:DUCKDB_PATH = (Resolve-Path $databasePath).Path
        Invoke-Gate 'dbt build' { & $Dbt build --project-dir dbt --profiles-dir dbt }
        Invoke-Gate 'Streamlit AppTest' { & $Python scripts/verify_streamlit.py }
        if (-not $SkipFreshness) {
            Invoke-Gate 'dbt source freshness' {
                & $Dbt source freshness --project-dir dbt --profiles-dir dbt
            }
        }
    }

    Write-Host ""
    Write-Host "All gates passed: $($passed -join ', ')" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($passed.Count -gt 0) {
        Write-Host "Gates that passed before the failure: $($passed -join ', ')" -ForegroundColor Yellow
    }
    exit 1
}
finally {
    Pop-Location
}
