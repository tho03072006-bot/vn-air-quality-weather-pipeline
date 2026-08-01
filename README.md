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

One command runs every gate. It calls no real API and no AWS endpoint:

```powershell
.\scripts\verify.ps1
```

The script stops at the first failure and names the gate that broke. Add
`-UseRealWarehouse` to build dbt against the real DuckDB file instead of the
demo fixture, or `-SkipDbt` to run only the Python gates.

The individual steps, if you prefer to run them by hand:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m pytest
python -m compileall -q airflow\dags dashboard
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

Three tabs:

- **Concentration and weather** — the raw µg/m³ view described above.
- **VN_AQI** — hourly and daily index, dominant pollutant, band distribution and
  health advice.
- **Pipeline health** — freshness and row counts read from the audit table.

## VN_AQI

The index follows [Quyết định 1459/QĐ-TCMT](https://cem.gov.vn/storage/news_file_attach/QD%201459%20TCMT%20ngay%2012.11.2019%20AQI.pdf)
(12 November 2019). PM2.5 and PM10 use the Nowcast weighted mean for the hourly
index and the 24-hour mean for the daily index; NO2 uses the highest 1-hour
mean; O3 takes the larger of its 1-hour and 8-hour sub-indices. The published
value is the maximum sub-index, and it is suppressed when neither PM10 nor
PM2.5 is available, as muc 2.1 requires.

Breakpoints live in `dim_vn_aqi_breakpoint` and bands in
`dim_vn_aqi_category`, so the legal reference is data rather than code. A dbt
singular test replays the worked examples printed in muc 2.3 of the decision,
which means a mistyped breakpoint fails the build.

Deviations from the decision (UTC day boundary, 8-hour completeness rule,
modeled-source caveat) are listed in
[docs/architecture.md](docs/architecture.md).

## Pipeline observability

Every run merges one row into `raw.pipeline_runs` keyed on `run_id + data_date`,
surfaced as `analytics.fct_pipeline_run` with duration, per-source row counts
and `is_latest_run_for_date`. dbt source freshness warns after 36 hours and
errors after 72.

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
