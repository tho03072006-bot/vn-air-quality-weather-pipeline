# Vietnam Air Quality & Weather Decision Platform

An end-to-end decision platform for Vietnam, with both a local development path
and a read-only public deployment target on Streamlit Community Cloud. It
preserves observed OpenAQ data separately from modeled Open-Meteo/CAMS data,
precomputes versioned 72-hour forecasts for all 34 province-level units effective
from 1 July 2025, builds tested DuckDB/dbt marts, and serves a Vietnamese
Streamlit dashboard. The deployment warehouse is a release asset, not a file
committed to git.

**Live:** <https://vn-air-quality-weather.streamlit.app/>

Verified against the deployed app rather than assumed: all 34 anchors carry a
forecast with none missing, no anchor is serving mismatched air and weather
vintages, and no page renders an operator shell command at a reader.

**Start here if you are picking this up:** [docs/handover.md](docs/handover.md)
records the verified state, what is deliberately not verified, and where to
continue.

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

### Layout checks in a real browser

`verify.ps1` is offline by contract, so the browser gate is separate. It needs a
running server, a browser binary, and one live API call on the custom-location
page:

```powershell
python -m pip install --editable ".[qa]"
python -m playwright install chromium
```

Then, with the app running from the project root in another shell:

```powershell
python scripts\verify_layout.py
```

It measures nine pages at 390x844 and 1280x800 for two faults `verify_streamlit.py`
structurally cannot see, because AppTest has no DOM and no layout engine: a chart
drawn wider than its container, and text clipped by a CSS ellipsis. Both have
shipped before, and the first recurred at a viewport its original fix did not cover.

Two accessibility gates run the same way:

```powershell
python scripts\verify_a11y.py       # WCAG 1.4.3 / 1.4.11 text contrast
python scripts\verify_keyboard.py   # WCAG 2.1.1 / 2.4.7 keyboard, 2.4.3 advisory
```

The contrast gate composites each text node's foreground against its ancestor
backgrounds; the keyboard gate presses Tab and measures what the keyboard actually
reaches and how focus looks. Both pass today. 2.4.3 (focus order) is reported but
does not fail the run — sections I and K of
[the audit register](docs/code-audit-and-risk-register.md) record why, along with
the five false-positive classes that had to be removed from these gates before
their output could be trusted.

All three browser gates run in CI with `--skip-live-api`, which drops the
custom-location page because its driver calls the real forecast API and CI is
offline by contract. Run them without the flag locally to cover all nine pages.

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

## Deployment

The Streamlit Community Cloud deployment target is deliberately simple: the app
only reads a prebuilt DuckDB warehouse. There is no dbt invocation at runtime, the
app never writes to DuckDB, and every database connection is opened with
`read_only=True`. The warehouse is not stored in git; it is published as the
`vn_air_quality_weather.duckdb` asset under the `demo-warehouse` release tag.

When the expected local file is absent, `dashboard/warehouse_source.py` downloads
the release asset once during app startup, writes it through a sibling temporary
file, and atomically moves it into place only after the download completes. When a
local warehouse already exists, or no deploy URL is configured, the module does
nothing and emits no local-development noise.

### Refresh architecture

`.github/workflows/refresh-demo-warehouse.yml` runs every six hours and performs
the write side away from the Streamlit runtime:

```text
download release asset
    -> add one real forecast vintage
    -> prune old forecast vintages
    -> dbt build
    -> compact into the upload file
    -> verify_streamlit.py against that exact file
    -> replace the release asset
```

The workflow intentionally runs only the forecast pipeline, which needs no API
key, so the workflow needs no repository secret.

The prune, dbt and final compaction steps cannot be reordered:

| Stage | Measured file size | Why it is in this position |
|---|---:|---|
| Input asset | 28.8 MB | Contains the accumulated forecast vintages before deployment pruning. |
| Prune old vintages | 13.8 MB | Must run before dbt; otherwise analytics are still built from all 12 vintages. Raw air-quality forecast rows fall from 176,256 to 14,688, raw weather forecast rows from 29,376 to 2,448, and distinct vintages from 12 to 1. |
| dbt build | 19.8 MB | Rebuilds analytics from the pruned raw layer, but DuckDB reuses freed pages instead of returning them to the file, so the file grows again. |
| Final compaction | 11.5 MB | Must run after dbt to reclaim the pages left behind by the analytics rebuild. Compacting only before dbt would publish the re-inflated file. |

### The filename is an invariant

The asset must remain named `vn_air_quality_weather.duckdb` from the source
warehouse through CI to the file downloaded by Streamlit. DuckDB derives a catalog
name from the file stem and bakes that catalog qualifier into stored view
definitions. `mart_current_conditions` and `mart_outdoor_decision_window` are
views, so renaming the asset makes every page that reads those marts fail with
`Catalog "..." does not exist`.

For that reason, `scripts/build_deploy_warehouse.py` accepts `--output-dir`, not a
caller-selected output filename. It always preserves the source filename and
verifies that both views can be read after every write.

### Data provenance in the deployment target

The deployment target has three distinct data paths. They must not be described as
one uniformly fresh source:

- **Province forecasts:** real Open-Meteo/CAMS forecast data, refreshed every six
  hours by CI.
- **Observed history:** real OpenAQ observations, but frozen at the warehouse seed
  date. CI does not run the historical pipeline because that pipeline requires an
  API key, so this part of the asset will become progressively older.
- **Custom location:** the **Địa điểm tùy chọn** page calls Open-Meteo directly at
  runtime and therefore returns fresh modeled data independently of the release
  warehouse.

### Deployment configuration

Community Cloud reads these names through Streamlit secrets; local shells may set
the same names as environment variables.

| Variable | Required where | Purpose |
|---|---|---|
| `DEMO_WAREHOUSE_URL` | Required for deployment | Direct download URL for the `demo-warehouse` release asset. |
| `DEMO_WAREHOUSE_TOKEN` | Only when the repository is private | Bearer token used to download the private repository's release asset. It is unnecessary when the asset is public. |
| `DUCKDB_PATH` | Local development | Overrides the local DuckDB path. When the file already exists, no release download occurs. |

In Streamlit Community Cloud settings, select **Python 3.11**. The project pins
`requires-python = ">=3.11,<3.12"` in `pyproject.toml`; choosing another Python
version can fail dependency installation with an error that does not mention
Python at all.

### Measured deployment gate

The release path was verified with the file that would be uploaded, not a different
local warehouse:

| Gate | Result |
|---|---|
| Ruff | clean |
| pytest | 313 passed, 1 skipped |
| dbt build | PASS=130, ERROR=0 |
| Streamlit | 16/16 |
| `verify.ps1` | exit 0 |

### Deliberately not production-ready

This is a public-demo deployment shape, not a production security baseline.
Security, backup and observability have not been completed. The Alerts page is a
preview only: it sends nothing and persists no alert history. No accuracy number is
published because no forecast-verification fact exists; the product reports
confidence without relabelling it as accuracy.

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

Raw object counts are split into attempted / created / reused. The split is
narrower than it sounds: the raw object key embeds the `run_id`, so a manual
replay mints a new uuid and always writes new objects, reporting created rather
than reused. Reuse appears when the same `run_id` writes the same payload twice,
which in practice means a retried Airflow task, where the `run_id` is Airflow's
and stays stable across attempts. That is the case worth distinguishing: a retry
that re-fetched nothing should not be reported as fresh ingestion. Verified
empirically — two manual runs of the same date both reported created 15, reused 0.

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
changes, and it submits the history filter form and fails if the default selection
returns no rows. The custom-location page performs no network call in its initial
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
