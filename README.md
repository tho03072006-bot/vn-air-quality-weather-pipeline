# Vietnam Air Quality & Weather Decision Platform

An end-to-end local decision platform for Vietnam. It preserves observed
OpenAQ data separately from modeled Open-Meteo/CAMS data, precomputes versioned
72-hour forecasts for all 34 province-level units effective from 1 July 2025,
builds tested DuckDB/dbt marts, and serves a Vietnamese Streamlit dashboard.

## Architecture

```text
OpenAQ observations + Open-Meteo/CAMS history and forecasts
    -> Airflow 3 -> immutable raw JSON (local/S3)
    -> dlt merge with forecast vintages -> DuckDB
    -> dbt facts/marts/tests -> Streamlit decision pages

Open-Meteo Geocoding + user WGS84 coordinates
    -> bounded Streamlit cache -> temporary modeled forecast (no warehouse write)
```

See [architecture and lineage](docs/architecture.md) for grains, keys and the
UTC/local-time contract.
See also the [serving data dictionary](docs/data-dictionary.md) and
[source attribution and limitations](docs/sources-and-limitations.md).

## Data-source contract

- OpenAQ v3 supplies station observations where a recent reporting sensor is
  available. These rows are labeled `source_type=observed`.
- Open-Meteo CAMS supplies modeled PM2.5, PM10, NO2, O3, SO2 and CO forecasts.
  These rows are labeled `source_type=modeled`.
- All 34 province anchors have modeled forecast coverage. Da Nang and every
  other place without suitable OpenAQ observations are labelled `MODELED_ONLY`;
  no synthetic monitoring station is created.
- Open-Meteo historical forecast supplies temperature, humidity, precipitation,
  wind speed and wind direction.
- Open-Meteo Geocoding supplies GeoNames-backed location search restricted to
  Vietnam. Search results are not treated as official 2025 administrative-code mappings.
- Provider-flagged measurements are excluded from the published concentration
  rather than averaged into it: a flag means the source does not stand behind the
  reading. `mart_city_air_quality_hourly` publishes `excluded_flagged_count`
  beside each value, and the withheld rows stay queryable in
  `mart_flagged_measurement_quarantine`. An hour whose only readings were flagged
  is dropped rather than published with a null concentration that coverage would
  still count as data.
- A serving forecast row carries exactly one air vintage and at most one weather
  vintage. `forecast_issued_at_utc` and `weather_forecast_issued_at_utc` are
  published separately with an `is_vintage_aligned` flag, because when the two
  disagree the consumer needs to see that rather than be shown the newer of the
  two. `forecast_issued_at_utc` is the time this system fetched the data, not the
  provider's model-run time, which the API does not return.
- Date columns state their calendar in their name: `_vn` for the Vietnam AQI
  business day (whose index day runs 01:00 to 00:00 the next day, per Quyết định
  1459) and `_utc` for a UTC calendar date. They must never be joined to each
  other.

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

Ingest one immutable 72-hour forecast vintage for all 34 province anchors:

```powershell
python -m vn_air_quality_weather.forecast_pipeline --all-provinces --hours 72
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
demo fixture, `-SkipDbt` to run only the Python gates, or `-SkipFreshness` to
skip only the freshness check. Freshness remains enabled for a real warehouse
by default, so stale production-like data fails verification as intended.

The individual steps, if you prefer to run them by hand:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m pytest
python -m compileall -q src dashboard airflow\dags scripts
python scripts\build_demo_warehouse.py `
    --database data\warehouse\demo.duckdb
$env:DUCKDB_PATH = (Resolve-Path "data\warehouse\demo.duckdb")
dbt build --project-dir dbt --profiles-dir dbt
python scripts\verify_streamlit.py
dbt source freshness --project-dir dbt --profiles-dir dbt
```

## Dashboard

The scheduled province views read serving marts. The optional custom-location
page calls Open-Meteo only after the user submits a place or coordinate and
caches the temporary result for 15 minutes without writing it to DuckDB. To
open a deterministic offline demo:

```powershell
python scripts\build_demo_warehouse.py --database data\warehouse\demo.duckdb
$env:DUCKDB_PATH = (Resolve-Path "data\warehouse\demo.duckdb")
dbt build --project-dir dbt --profiles-dir dbt
python -m streamlit run dashboard\app.py
```

For the real warehouse, set `DUCKDB_PATH` to
`data\warehouse\vn_air_quality_weather.duckdb` instead.

Pages: **Hôm nay**, **Dự báo 24–72 giờ**, **Địa điểm tùy chọn**,
**Bản đồ Việt Nam**, **So sánh địa điểm**, **Lịch sử**, **Cảnh báo**,
**Độ tin cậy dữ liệu** and **Pipeline health**. Province views support all 34
anchors; the custom page supports Vietnam place search and validated WGS84
coordinates. Every modeled view shows source, coverage and confidence labels
and avoids presenting the planning heuristic as official VN_AQI.

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

The daily business window is 01:00–00:00 `Asia/Ho_Chi_Minh`; timestamps remain
stored in UTC. Remaining interpretation choices (8-hour completeness and the
modeled-source caveat) are listed in
[docs/architecture.md](docs/architecture.md).

## Pipeline observability

Both pipelines merge one row into `raw.pipeline_runs` keyed on
`run_id + data_date`, surfaced as `analytics.fct_pipeline_run` with
`pipeline_name`, duration, per-source row counts and `is_latest_run_for_date`.

A run outcome is not a boolean. A national forecast run touches 34 independent
anchors, so `status` records `SUCCESS`, `PARTIAL` or `FAILED` alongside
requested/succeeded/failed location counts. One anchor timing out costs that
anchor, not the run: whatever landed is loaded and the run is audited as
`PARTIAL`. Only a run where every anchor failed raises, so Airflow retries a
dead run rather than a merely incomplete one. Failed anchor keys are named in
the audit row and printed by the CLI as a ready-to-paste `--province` rerun.

Raw object counts are split into attempted / created / reused. The raw layer is
content-addressed, so replaying a day mostly reuses what is already on disk;
counting every write as "created" made a replay look like fresh ingestion.

`is_latest_run_for_date` is partitioned by `pipeline_name` as well as date.
Without that the historical and forecast runs that share a date compete for one
"latest" flag and whichever finished second disappears from the health panel.

Both DAGs declare a single-slot Airflow pool (`warehouse_writer`) on every task
that writes DuckDB or runs dbt. `max_active_runs` is per DAG, so it does not
prevent the two DAGs overlapping, and DuckDB accepts one writer at a time. This
is an accepted limitation of a local DuckDB deployment, not a design that
scales.

The verification and CI gates check freshness for history, forecast and
run-audit sources. `scripts/verify_streamlit.py` does more than execute the nine
pages: each page declares the text, widget labels and data tables that must be
present, plus forbidden strings such as `nan µg/m³`, and the script drives the
forecast horizon and the map metric switch and fails unless the output actually
changes. The custom-location page performs no network call in its initial
state, keeping CI deterministic. `scripts/benchmark_dashboard.py` measures
server-side render time per page; results are recorded in
[docs/ui-design-spec.md](docs/ui-design-spec.md).

## Airflow 3 with Docker Compose

```powershell
docker compose -f airflow\docker-compose.yml build
docker compose -f airflow\docker-compose.yml up airflow-init
docker compose -f airflow\docker-compose.yml up -d
```

Open http://localhost:8080. `vn_air_quality_weather_daily` handles historical
ingestion/backfill; `vn_air_quality_weather_forecast` refreshes all 34 anchors
every six hours without requiring OpenAQ credentials. This Compose deployment is a
learning/local configuration, not a production security baseline. A terminal
task failure emits one `airflow_task_failure` log record with DAG, task, run,
data-interval and exception fields for alerting and diagnosis.

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
- Province forecasts use one representative WGS84 anchor and are not street-level.
- Custom-coordinate forecasts are temporary, model-only, and cache for at most
  15 minutes; saved-place persistence and official commune-code mapping are not implemented.
- The outdoor score is an explainable planning heuristic, not a medical index.
- Forecast confidence is initially lead-time based; accuracy claims require
  30–60 days of matched forecast and observation history.
- Telegram alert evaluation has freshness, quiet-hours, cooldown and
  idempotency controls; production delivery/history persistence is a later
  deployment step and secrets must remain in `.env`.
- No annual claim is made that Hanoi ranked fifth globally in 2026; available
  IQAir ranking evidence was time-specific and cannot support a full-year claim.

## Security

`.env`, `.venv`, raw JSON, DuckDB files, logs, dbt artifacts and cloud
credentials are ignored. CI uses deterministic generated data and needs no
production secret.
