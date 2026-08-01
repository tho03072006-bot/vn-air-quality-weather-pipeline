# Vietnam Air Quality & Weather Data Pipeline

An end-to-end learning project that collects hourly weather and air-quality
data for Hanoi, Ho Chi Minh City, and Da Nang, preserves immutable raw API
responses, loads DuckDB incrementally, builds tested dbt marts, schedules daily
runs with Airflow 3, and serves a Streamlit analytics dashboard.

## Architecture

```text
Open-Meteo + OpenAQ -> Airflow 3 -> raw JSON (local/S3)
    -> dlt merge -> DuckDB -> dbt models/tests -> Streamlit
```

See [architecture and lineage](docs/architecture.md) for grains, keys and the
UTC/local-time contract.

## Data-source contract

- OpenAQ v3 supplies station observations where a recent reporting sensor is
  available. These rows are labeled `source_type=observed`.
- Open-Meteo CAMS supplies spatially complete modeled PM2.5, PM10, NO2 and O3.
  These rows are labeled `source_type=modeled`.
- Da Nang had no OpenAQ location in the validated city bounding box, so its
  complete pollutant series comes from CAMS and is never presented as a station
  measurement.
- Open-Meteo historical forecast supplies temperature, humidity, precipitation,
  wind speed and wind direction.

## Quick start on Windows PowerShell

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
& ".\.venv\Scripts\Activate.ps1"
python -m pip install --upgrade --editable ".[dev]"
Copy-Item ".env.example" ".env"
```

Put the OpenAQ key in `.env`. Keep `RAW_BACKEND=local` until AWS is configured.

Run one UTC day and build dbt:

```powershell
python -m vn_air_quality_weather.pipeline --date 2026-07-27
```

Backfill an inclusive date range:

```powershell
python -m vn_air_quality_weather.pipeline `
    --start-date 2026-07-25 `
    --end-date 2026-07-27
```

Rerunning the same interval is safe: dlt merges on composite natural keys and
dbt grain tests reject duplicates.

## Offline validation

The CI path does not call real APIs or AWS:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m pytest
python scripts\build_demo_warehouse.py `
    --database data\warehouse\demo.duckdb
$env:DUCKDB_PATH = (Resolve-Path "data\warehouse\demo.duckdb")
dbt build --project-dir dbt --profiles-dir dbt
```

## Dashboard

The dashboard reads `analytics.mart_*` only:

```powershell
$env:DUCKDB_PATH = (Resolve-Path "data\warehouse\vn_air_quality_weather.duckdb")
streamlit run dashboard\app.py
```

Filters cover UTC date range, city, pollutant and data type. KPIs cover average
and maximum concentration, available hours, coverage, temperature, humidity and
wind. Charts show trends, city comparison, hourly pattern, humidity/wind
relationships, rainy versus dry hours, and missing-data coverage.

## Airflow 3 with Docker Compose

```powershell
docker compose -f airflow\docker-compose.yml build
docker compose -f airflow\docker-compose.yml up airflow-init
docker compose -f airflow\docker-compose.yml up -d
```

Open http://localhost:8080. The DAG uses its Airflow data interval instead of
`datetime.now()` and supports catchup/backfill. This Compose deployment is a
learning/local configuration, not a production security baseline.

## AWS S3

Set `RAW_BACKEND=s3`, `AWS_REGION`, `AWS_S3_BUCKET`, and optionally
`AWS_PROFILE` only after following [AWS setup and cost guardrails](docs/aws-setup.md).
S3 is usage-priced; create a budget alert before enabling cloud writes.

## Metric definitions

- Average concentration: arithmetic mean of the filtered hourly city-level
  concentration in µg/m³.
- Maximum concentration: highest filtered hourly concentration.
- Hours with data: distinct UTC hours returned by the selected source type.
- Coverage: distinct hours divided by 24 for each UTC city/date/source slice,
  capped at 100%.
- Weather metrics: hourly weather values joined on city and normalized UTC hour.

## Limitations and responsible use

- Correlation does not demonstrate causation.
- This project is for learning and analysis and is not medical or public-health
  advice.
- OpenAQ coverage varies by station, pollutant and time period.
- CAMS is modeled data, not a replacement for a regulatory monitor.
- No annual claim is made that Hanoi ranked fifth globally in 2026; available
  IQAir ranking evidence was time-specific and cannot support a full-year claim.

## Security

`.env`, `.venv`, raw JSON, DuckDB files, logs, dbt artifacts and cloud
credentials are ignored. CI uses deterministic generated data and needs no
production secret.
