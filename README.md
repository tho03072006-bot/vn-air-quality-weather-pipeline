# Vietnam Air Quality & Weather Data Platform

An end-to-end decision platform for Vietnam. It preserves observed OpenAQ data
separately from modeled Open-Meteo/CAMS data, precomputes versioned 72-hour forecasts
for all 34 province-level units effective from 1 July 2025, builds tested DuckDB/dbt
marts, and publishes fifteen serving relations into SQL Server for the web
application to read.

**The user interface lives in a separate repository.** `../vn-air-web` is a Spring
Boot application deployed as a war to Tomcat 11; this repository is the pipeline that
feeds it. The two are joined at exactly one place -- the SQL Server serving database --
and at one contract, described under [SQL Server serving database](#sql-server-serving-database).

**Start here if you are picking this up:** [docs/handover.md](docs/handover.md)
records the verified state, what is deliberately not verified, and where to continue.

## Architecture

```text
OpenAQ observations + Open-Meteo/CAMS history and forecasts
    -> Airflow 3 -> immutable raw JSON (local/S3)
    -> dlt merge with forecast vintages -> DuckDB
    -> dbt facts/marts/tests
    -> publish 15 relations -> SQL Server -> vn-air-web (Spring, Tomcat)
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
python -m pip install --upgrade --editable ".[dev,etl]"
Copy-Item ".env.example" ".env"
```

`etl` is required for pipeline and dbt work. Pytest also imports
`forecast_pipeline`, `pipeline` and `duckdb_loader`, so a test environment needs
both `dev` and `etl`; `dev` alone is intentionally insufficient. A dashboard-only
runtime installs the project without extras.

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
`-UseRealWarehouse` to build dbt against the real DuckDB file instead of the demo
fixture, `-SkipDbt` to run only the Python gates, or `-SkipFreshness` to skip only the
freshness check. Freshness remains enabled for a real warehouse by default, so stale
production-like data fails verification as intended.

The individual steps, if you prefer to run them by hand:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m pytest
python -m compileall -q src airflow\dags scripts
python scripts\build_demo_warehouse.py --database data\warehouse\demo.duckdb
$env:DUCKDB_PATH = (Resolve-Path "data\warehouse\demo.duckdb")
dbt build --project-dir dbt --profiles-dir dbt
dbt source freshness --project-dir dbt --profiles-dir dbt
```

**The browser gates left with the pages they measured.** `verify_layout.py`,
`verify_a11y.py` and `verify_keyboard.py` drove a Playwright browser over the
Streamlit pages, and the four defect classes they existed to catch -- a chart wider
than its container, text clipped by CSS, a contrast ratio below WCAG AA, a control Tab
cannot reach -- are now the web application's to check. They are, in
`vn-air-web/src/test/java/vn/aqw/web/a11y/`, which runs on every build there rather
than only by hand here. What is not carried over is recorded in that project's README
under *What the tests could not see, and what found it*.

## The user interface, and where it went

This repository no longer serves a UI. Both surfaces it used to carry were replaced by
`../vn-air-web` and were removed, with their tests, in the same commit:

| Removed | What it was | What replaced it |
|---|---|---|
| `dashboard/` | nine-page Streamlit app | the nine Thymeleaf pages in vn-air-web |
| `api/main.py` | read-only FastAPI view of the marts at `/v1/*` | **nothing** |

**The API has no successor, and that is a loss worth naming rather than glossing.**
`/v1/current`, `/v1/forecast/{province_code}` and their `disclosure` object were the
only machine-readable view of these marts. vn-air-web serves HTML and one status
endpoint; a consumer that wanted the numbers without the UI now has to read SQL Server
directly, which means it also has to be trusted with a database credential. Restoring
a JSON API is a real piece of work, not a checkbox, and it is not scheduled.

**What survived the removal, and why.** `dashboard/data_access.py` moved to
`src/vn_air_quality_weather/serving_queries.py` rather than being deleted. It had
stopped being a dashboard's data layer some time before the dashboard was retired: the
Spring repositories restate all sixteen of its queries in T-SQL, and
`RepositoryFidelityTests` proves that restatement correct by comparing it against what
these functions return. Deleting it would have left that gate passing with nothing to
compare against, which is a worse outcome than the drift it exists to catch. See the
module docstring for the rules that now apply to it.

**The public Streamlit Community Cloud deployment is gone with it**, along with
`refresh-demo-warehouse.yml`, which rebuilt the release asset that deployment read,
and `verify-live-app.yml`, which checked the live URL. vn-air-web runs on a local
Tomcat and is not a public replacement for it. Anyone who wants the public demo back
is restoring a deployment, not reverting a file.

## SQL Server serving database

DuckDB stays the analytical warehouse. A separate copy of the fifteen relations the
web application reads is published into SQL Server, because DuckDB admits one writer
or many readers and never both: a web process holding a read pool on the warehouse
file takes the lock the 02:00 DAG needs, and the symptom is a broken pipeline rather
than a slow page.

```powershell
python -m pip install --editable ".[etl]"
# Credentials come from the environment, never from a committed file. See below.
python scripts\publish_to_sqlserver.py
python scripts\verify_sqlserver_publish.py
```

### Two logins, and a gate that proves the difference

`sa` was a bootstrap credential and is no longer used by either project. It let the web
application drop tables and let the publisher read every database on the instance, and
the only thing between a mistyped query and the serving copy was that nobody had yet
made the mistake.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create_serving_logins.ps1 -Persist
python scripts\verify_serving_logins.py
```

| Login | Holds | Used by |
|---|---|---|
| `vn_air_publish` | `CREATE TABLE`, and `ALTER, SELECT, INSERT, UPDATE` on `dbo` | this project's publish |
| `vn_air_web` | `SELECT` on `dbo`, with `INSERT, UPDATE, DELETE, ALTER, REFERENCES` explicitly **denied** | vn-air-web's Hikari pool |

The DENY is not redundant. Granting only SELECT already leaves the reader unable to
write; the DENY is what keeps it that way after somebody adds the login to
`db_datawriter` to solve an unrelated problem, because DENY outranks every GRANT it
meets. The grants are schema-level rather than per-table because the publisher drops
and recreates all fifteen tables on every run, and a grant made to a table object dies
with that object -- the page would go dark on the first tick of the five-minute timer.

**No password reaches a file.** `create_serving_logins.ps1` reads them from the
environment, or prompts without echo, and hands them to `scripts/sql/create_serving_logins.sql`
hex-encoded. That is not decoration: sqlcmd substitutes a scripting variable
*textually*, before SQL Server parses anything, so a password containing a single quote
spliced into `N'$(WEB_PASSWORD)'` ends that literal early and turns the rest of the
password into SQL. Quoting inside the script cannot repair damage done one layer above
it. Hex has no character that can close a literal, so the failure is structurally
unavailable rather than carefully avoided. The first version of the script did it the
obvious way and broke on the first generated password, which is why the punctuation in
these passwords is chosen to find that class of defect rather than to avoid it.

`verify_serving_logins.py` connects **as each login** and tries both sides of every
boundary, because a GRANT that ran without error says what was requested, not what was
granted. A refusal only counts when it carries one of SQL Server's permission-denied
codes, so an unreachable server or a wrong password is a reported failure and not a
boundary silently banked as proved. The one exception is `DROP TABLE`, which answers
3701 -- *"because it does not exist or you do not have permission"* -- deliberately
conflating the two so a probe cannot map a schema it has no rights to; the check reads
the table first, so a refusal cannot be the table simply being absent. Verified to
fail: granting the reader `INSERT, UPDATE, DELETE, ALTER` turns four checks red and
exits non-zero, and re-running the creation script turns them green again.

The publish drops, recreates and refills every table inside one transaction, so the
serving copy is always exactly one dbt build and never a blend of two. All fifteen
relations together are about nine thousand rows, which is why a full reload is
cheaper than a merge key per table and has nowhere for a divergence to hide. The
target schema is derived from `information_schema` and from measuring the data, so
there is no hand-written DDL to drift from the models dbt already owns.

**Run the publish on a short timer, not only after a DAG.** Five of the fifteen
relations are dbt views that read `current_timestamp` on every query, and they are
views on purpose — `mart_current_conditions` picks the forecast hour nearest to now,
and both window models carry a comment recording that they were tables once and
froze at build time, recommending hours that had already elapsed. Copying a live view
into a static table reintroduces exactly that defect. The answer is cadence: the
publish costs about a second, so it runs every few minutes and the frozen `as_of_utc`
is never older than that. Rewriting the five models as T-SQL views over the published
tables would instead put a second copy of the decision logic in a second language,
free to disagree with the first.

`dbo.warehouse_version` is the single control row a reader polls. `version_id`
advances only when the *content* changed — a hash over every published value except
the clock-derived ones — so a five-minute timer does not look like new data every
five minutes. `refreshed_at` always moves, which is what distinguishes a publisher
that is running and finding nothing new from one that has stopped.

### Which SQL Server, and why that question has a wrong answer

This machine has three SQL Server instances -- `MSSQLSERVER`, `SQLEXPRESS` and
`SQLEXPRESS01` -- and all three are statically configured for port 1433. Only one can
bind it; the others fail to start with *"Only one usage of each socket address"* and
that message goes to the Windows System event log, not anywhere a developer is looking.

`SQLSERVER_HOST=localhost` with `SQLSERVER_PORT=1433` addresses the instance by port,
so **which database this project reaches depends on which service started first.**
`vn_air_quality` lives on `SQLEXPRESS` (`D:\CSDL\MSSQL16.SQLEXPRESS`). Start
`MSSQLSERVER` and it takes 1433, the publish then authenticates against a server that
has never heard of this database, and the error reads *"Cannot open database
vn_air_quality requested by the login. The login failed."* -- which sends the reader to
check a password that was never wrong.

```powershell
Get-Service | Where-Object { $_.Name -like 'MSSQL*' } | Select-Object Name, Status
Get-WinEvent -LogName System -MaxEvents 200 |
    Where-Object { $_.Message -like '*SQLEXPRESS*' } | Select-Object -First 3 TimeCreated, Message
```

Giving each instance its own port would remove the ambiguity. That is a change to
somebody's other coursework as well as to this project, so it is recorded here rather
than made silently.

### Who publishes, and when

Three callers run the same function, `vn_air_quality_weather.sqlserver_publish.publish_serving_marts`.
It lives in the installed package rather than in `scripts/` because the Airflow image
installs the package and not this directory, and a DAG task and a shell must not run
two copies of it.

| Caller | Cadence | Why |
|---|---|---|
| `scripts\publish_to_sqlserver.py` | by hand | development and one-off checks |
| `publish_serving_copy` task in both DAGs | after each run | a finished run reaches the page in seconds |
| Windows scheduled task | every 5 minutes | keeps the copied `current_timestamp` fresh |

Register the timer once. It runs as the current user while logged on, so no password
is stored:

```powershell
powershell -ExecutionPolicy Bypass -File scripts
egister_publish_timer.ps1
powershell -ExecutionPolicy Bypass -File scripts
egister_publish_timer.ps1 -Remove
```

It appends one line per tick to `logs\publish_to_sqlserver.log` and trims the file
rather than rotating it.

The Airflow image installs `msodbcsql18`, because pyodbc is a binding around a system
driver rather than a self-contained client: without it `pip install pyodbc` succeeds
and the task fails at run time with an error naming a missing shared object. Inside a
container `SQLSERVER_HOST` is overridden to `host.docker.internal` in
`docker-compose.yml` — `localhost` from `.env` is right from PowerShell and resolves
to the container itself in Docker. `publish_serving_copy` holds the single-slot
warehouse pool although it only reads, because DuckDB lets a reader block a writer.

`verify_sqlserver_publish.py` compares both databases value by value rather than
counting rows, and exits non-zero on any difference, so it can gate a pipeline run.
Two defects it has already caught are worth naming, because neither raised an error
on its own:

- `[column]` is list construction in DuckDB, not identifier quoting. Every scalar
  came back wrapped in a one-element list and every measured string width came back
  as 1.
- With `fast_executemany`, pyodbc ignores an aware datetime's own offset and stamps
  the client machine's onto the wall clock, storing 06:00 UTC as 06:00+07:00. The
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
