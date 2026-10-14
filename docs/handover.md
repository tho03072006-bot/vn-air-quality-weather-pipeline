# Handover

State of the project as delivered. Written to be read by someone who was not here,
so it says what is verified, what is not, and what to do next — in that order.

Last commit at handover: see `git log -1`. Working tree clean, nothing staged.

## 1. What this is

An end-to-end pipeline for Vietnamese air quality and weather: hourly observations
and modelled data for three cities, plus 72-hour modelled forecasts for all 34
province-level units. Local DuckDB warehouse, dbt for transformation, Airflow 3 for
scheduling, and fifteen serving relations republished into SQL Server every five
minutes.

**The user interface is a different repository.** `../vn-air-web` is a Spring Boot
application, packaged as a war and deployed to Tomcat 11, that reads the SQL Server
copy through a read-only pool. The Streamlit dashboard and the FastAPI read API that
used to live here were removed in P6 once it replaced them; the pipeline README says
what was lost with them, and it is not nothing.

Read next: [architecture](architecture.md) for grains and keys,
[sources and limitations](sources-and-limitations.md) for what the data can and
cannot support.

## 2. Verified state

Every figure below was measured, not estimated. The **Measured** column says when,
because a number carried forward from an earlier session and a number taken just now
are different kinds of claim, and this project has twice shipped a statement that was
true when written and false when read.

| Gate | Command | Result | Measured |
|---|---|---|---|
| Format | `ruff format --check .` | 67 files clean | 2026-10-14 |
| Lint | `ruff check .` | pass | 2026-10-14 |
| Unit tests | `pytest` | **277 passed, 1 skipped**, coverage 88.19% | 2026-10-14 |
| dbt | `dbt build` on the demo warehouse `verify.ps1` builds | **PASS=172, ERROR=0** | 2026-10-14 |
| Serving publish | `publish_to_sqlserver.py` | 11,548 rows, 15 relations, as `vn_air_publish` | 2026-09-08 |
| **Permission boundary** | `verify_serving_logins.py` | **14/14** — reader refused 5 writes, writer allowed 6 operations and refused CREATE LOGIN | 2026-09-08 |
| **Web application** | `scripts\mvn.ps1 -Offline` (in `../vn-air-web`, JDK 25) | **151 passed, 0 skipped**, including 18 fidelity comparisons against the frozen fixture | 2026-10-14 |
| **Web, in a browser** | `scripts\verify_browser.py` (in `../vn-air-web`) | **36/36**: 9 pages × 390/1280 px × light/dark, no overflow, one h1 each | 2026-10-14 |
| **live.js** | `node --test scripts/test-live.cjs`, `verify_live_browser.py` | **26/26**, **4/4** Chromium scenarios | 2026-10-14 |
| **Deployed war** | 9 pages + `/live/stream` + `/actuator/health` over HTTP on Tomcat 11.0.25 | **14/14 URLs 200**, `servingDatabase: UP`, SSE `listeners: 1` | 2026-09-08 |
| Byte-compile | `compileall src airflow/dags scripts` | exit 0 | 2026-10-14 |
| Compose | `docker compose -f airflow/docker-compose.yml config` | exit 0 | carried forward |
| Airflow DagBag | `airflow dags list-import-errors` | **0 import errors**, both DAGs registered | carried forward |
| Airflow pool | `airflow pools list` | `warehouse_writer` present, **1 slot** | carried forward |
| **Airflow scheduled execution** | the scheduler, catching up | forecast run **success** end to end to SQL Server; daily catch-up **66 runs success** after the closed-client fix (every daily run from 2026-08-19 had failed in `extract_store_and_load`) | 2026-10-14 |
| Secret scan | `git grep` over tracked files | 2 known-benign hits | carried forward |

**The test count fell from 344 to 178, and that is the removal, not a regression.**
166 of them tested the Streamlit pages and the FastAPI read API, and they were deleted
in the same commit as the code they covered. The gates that remain cover the pipeline;
the gates that replaced the deleted ones live in `../vn-air-web` and are counted in the
row above.

**Coverage fell from 89.51% to 80.78% for a mechanical reason worth knowing.**
`serving_queries.py` moved into the measured package when it moved out of `dashboard/`,
and it is exercised only by `test_query_fingerprints.py`, which needs a warehouse. It
adds 86 statements at 25% coverage. The threshold is 70% and the module is not
untested — the Java fidelity suite reads it 17 times a build — but `--cov` cannot see
that, so the number understates it.

**`ruff format --check` was already failing before P6.** `sqlserver_publish.py` and
`verify_sqlserver_publish.py` carried formatting deviations that `ruff check` does not
look for, so a handover reporting "Ruff clean" was true of the linter and false of the
formatter. Both are formatted now and both commands are green.

The Airflow rows are carried forward from the session that measured them. The stack
was not brought up again here, so treat them as last-known-good rather than as
current.

`.env` is not tracked; only `.env.example` is. The two secret-scan hits are the
deliberate fake `apikey=super-secret` in `tests/test_forecast_run.py`, which exists
precisely to prove `redact()` strips query strings from a persisted error summary.
Recorded here so a future scan does not read them as a leak.

Baseline when this work started: 82 tests, 86.40% coverage, 108 dbt checks, and a
dashboard script that only checked for exceptions. The dashboard is gone; the dbt
checks are 171.

## 3. How to run it

```powershell
cd "D:\VS Code\vn-air-quality-weather-pipeline"
& ".\.venv\Scripts\Activate.ps1"
python -m pip install --upgrade --editable ".[dev,etl]"
```

`etl` is required even for pytest: the suite imports `forecast_pipeline`, `pipeline`
and `duckdb_loader`, whose import chains need boto3 and dlt. There is no longer an
extras-free runtime to install for — the `api` and `qa` extras went with the read API
and the browser gates that used them.

Everything offline, in one command — no API call, no AWS call:

```powershell
.\scripts\verify.ps1
```

### The serving database, which everything downstream depends on

**Start `SQLEXPRESS`, not `MSSQLSERVER`.** Three instances on this machine are all
statically configured for port 1433 and only one can bind it; `vn_air_quality` lives on
`SQLEXPRESS`. Starting the wrong one produces *"Cannot open database vn_air_quality
requested by the login. The login failed"*, which reads as a credential fault and is
not one. See the README section *Which SQL Server, and why that question has a wrong
answer*.

```powershell
Start-Service 'MSSQL$SQLEXPRESS'          # elevated
python scripts\verify_serving_logins.py   # 14 checks; proves the two logins are minimal
python scripts\publish_to_sqlserver.py
python scripts\verify_sqlserver_publish.py
```

Credentials come from the environment, not from `.env`:
`SQLSERVER_USER=vn_air_publish` and `SQLSERVER_PASSWORD` set to the value in
`VN_AIR_PUBLISH_PASSWORD`. Create or rotate both logins with
`scripts\create_serving_logins.ps1 -Persist`.

The five-minute republish runs as the scheduled task `VN Air - publish serving marts`.
Confirm it is `Ready` and not `Disabled` before concluding the data is stale:

```powershell
Get-ScheduledTask -TaskName 'VN Air - publish serving marts' | Select-Object TaskName, State
powershell -ExecutionPolicy Bypass -File scripts\register_publish_timer.ps1
```

### Refresh the data

```powershell
python -m vn_air_quality_weather.forecast_pipeline --all-provinces
python -m vn_air_quality_weather.pipeline --date 2026-08-07
```

Both run `dbt build` at the end. The forecast run needs no API key; the historical run
reads `OPENAQ_API_KEY` from `.env`.

### The fingerprints the web application is measured against

Both sides read one **frozen snapshot**, never the live warehouse or serving copy.
`data/fixtures/serving_snapshot/` holds the serving marts materialised in a single
DuckDB transaction at a pinned clock; the real publisher loads it into the isolated
database `vn_air_quality_fidelity`, and the fingerprints are exported from the same
snapshot. The five-minute timer and the hour turning therefore cannot change the
expected values. Fingerprints are per column and per row (a digest keyed by each
query's natural key), with floats canonicalised identically in Python and Java
(exact binary value, HALF_EVEN, six places).

```powershell
sqlcmd -S tcp:localhost,1433 -E -C -b -i scripts\sql\create_fidelity_fixture_db.sql   # once
python scripts\export_query_fingerprints.py --load-fixture-db --out ..\vn-air-web\src\test\resources\query_fingerprints.json
cd ..\vn-air-web ; powershell -ExecutionPolicy Bypass -File scripts\mvn.ps1 -Offline
```

Add `--refresh-snapshot` to the export when a query in
`src/vn_air_quality_weather/serving_queries.py` or a serving mart changes: it rebuilds
the snapshot from the warehouse at a new pinned clock. The loader accepts only the
literal database name `vn_air_quality_fidelity`.

### Airflow

```powershell
powershell -ExecutionPolicy Bypass -File scripts\preflight.ps1   # read-only; prints the fix for each FAIL
docker compose -f airflow\docker-compose.yml up airflow-init
docker compose -f airflow\docker-compose.yml up -d
powershell -ExecutionPolicy Bypass -File scripts\verify_airflow_scheduling.ps1
```

The UI is at http://localhost:8081 — host 8081, because Tomcat holds 8080. Run compose
from a shell opened after the publish credential was set: the containers receive
`SQLSERVER_USER`/`SQLSERVER_PASSWORD` from that shell, not from `.env`.

**Expect the forecast DAG to run on its own as soon as the scheduler is back.** It is
unpaused. On 2026-10-11, after eight weeks down, the scheduler first finished a run left
`running` since 2026-08-16 and then ran the 12:00 UTC interval, before a triggered run
could start. Each publishes, so `version_id` advances more than once.

**Open risk: the five-minute timer and a DAG can touch the warehouse at once.** The DAG
writes DuckDB inside the Docker VM; the timer reads the same file from Windows. The
single-slot `warehouse_writer` pool serialises tasks inside Airflow only, and nothing here
has shown whether DuckDB's file lock holds across the bind mount. On 2026-10-11 the timer
was kept disabled until the triggered run finished, and a backup of the warehouse was
taken first; in steady state the forecast DAG runs every six hours while the timer runs
every five minutes, so they will overlap eventually. Untested, not known to be safe.

For a one-off daily-DAG test of data date `2026-08-07`, pass the following interval end
(not the data date) as the logical date:

```powershell
docker compose -f airflow\docker-compose.yml exec airflow-scheduler airflow dags test vn_air_quality_weather_daily 2026-08-08T02:00:00+00:00
```

### The web application

Its own README carries the deployment steps. In short: `mvn clean package`, copy the war
to `D:\WEB\apache-tomcat-11.0.25-windows-x64\apache-tomcat-11.0.25\webapps\`, and start
Tomcat. Credentials reach it through `bin\setenv.bat`, which maps `SQLSERVER_USER` and
`SQLSERVER_PASSWORD` onto the read-only login without containing the password itself.

## 4. What was fixed, and how it was proved

Eight correctness defects and four display defects. The proof matters as much as
the fix, because in three cases the first version of the test could not fail.

| Area | Defect | Evidence it is fixed |
|---|---|---|
| Forecast vintage | A serving row mixed pollutants from different model runs behind a `max()` timestamp | 66 mixed-vintage hours before, 0 after |
| Flagged data | Provider-flagged readings were averaged into the published value while a column claimed they were excluded | 3 grains where the published value would differ |
| Forecast audit | Forecast runs were never audited at all | SUCCESS/PARTIAL/FAILED rows now written and tested |
| Partial failure | One failing anchor discarded all 34 | A run with one failure loads the other anchors |
| Raw counting | Every write reported as "created" | created/reused split, verified against two real runs |
| Concurrency | Two DAGs could write DuckDB at once | Airflow confirms `warehouse_writer` on all four writing tasks |
| Freshness | "Current conditions" froze at dbt build time | `as_of_utc` advances between two queries seconds apart |
| Date naming | A Vietnam business date shipped as `data_date_utc` | Test recomputes every `_utc` column from its own timestamp |
| Charts | Every time series drew nothing (`datetime64[us]`) | Timeline renders with curve, threshold and axis labels |
| KPIs | Values truncated to `75…` | Reads `49.8`, unit moved into the label |
| Map legend | Printed `:#16a34a-badge[0-25]` as text | Coloured chips render |
| Facet grid | 1021px inside a 790px column | 641px inside 790px |
| Facet grid, narrow | 641px inside a 327px column at 390px wide, nothing scrolled, so three of six panels were unreachable | Per-pollutant charts with no pixel width; 0px overflow at 390 and 1280 |
| Coverage strip | A day with no rows produced no group, so the page said "every day has a full 24 hours" while 10 of 12 days held nothing | Reindexed over the selected range; reports 20 empty location-days |
| History default | Defaulted to NO2 + observed, a pair with zero rows, so a first submit always looked like a broken pipeline | Defaults to PM2.5; the default submit returns data |
| KPI labels | Moving the unit into the label to save the value moved the truncation onto the label: 183px of text in a 161px box | Labels wrap; 0 clipped elements at either viewport |

## 5. Not verified — read this before trusting anything

**Both previously unseen pages have now been driven and measured.** History's
coverage strip was reached by submitting the filter form; custom location's charts
were reached through coordinate entry, which calls the live forecast API. Four
defects came out of that, all listed in §4, and none of them raised an exception or
failed a test — the coverage strip was stating something false, and the default
filter returned nothing.

**The two defect classes now have an automated guard.** `scripts/verify_layout.py`
drives Chromium through Playwright and measures, per page and per viewport, whether
any chart is drawn wider than its container and whether any text is clipped by CSS.
Both arms are proven discriminating rather than assumed: the chart arm was run
against the old faceted spec and reported 641px and 635px inside a 326px container,
and the text arm found four clipped KPI labels before they were fixed.

It is **not** part of `verify.ps1`, which is contractually offline: it needs a
running server, a browser binary, and — on the custom-location page — a live API
call. Run it separately:

```powershell
streamlit run dashboard\app.py     # from the project root, in another shell
python scripts\verify_layout.py
```

Install with `pip install -e ".[dev,etl,qa]"` then
`python -m playwright install chromium`. The `qa` extra is kept out of `dev` so
`dev` stays installable offline; `etl` remains separate because transform and
ingestion engines do not belong in the read-only dashboard runtime.

**Screenshots still do not exist.** The Browser pane does not composite frames in
this environment, so `computer{action:"screenshot"}` times out and nothing has been
seen as an image. This turned out to matter less than it appeared: both defect
classes are geometry, and geometry is measurable. Contrast ratios and typography
remain unverified by eye.

**Performance is measured server-side only.** The numbers in
[ui-design-spec.md](ui-design-spec.md) exclude browser paint, WebGL setup for the
map, and client-side Altair rendering.

**The gap is now fully attributed, and no accuracy figure is published.** Those two
sentences are both true, and keeping them apart is the whole of finding O. The
published model-station gap decomposes exactly:

```
F_anchor − O_station = (F_anchor − M_anchor)   forecast drift
                     + (M_anchor − M_station)  representativeness
                     + (M_station − O_station) model offset at the station
```

All three terms are measured. `mart_forecast_vs_analysis` holds the first and it sits
near 1.0 with no growth across lead bands, so the gap is not the forecast being
wrong. `mart_station_representativeness` holds the other two, and
`assert_decomposition_identity_holds` fails if they stop reconstructing the whole.

**Attribution is not accuracy, and this is where it would be easiest to overclaim.**
Knowing how the gap divides says nothing about whether either number is right. The
model-offset term is measured against **one station per city, in two cities**, and
cannot speak for the other thirty-two provinces — which have no station at all, and
whose absence must never read as agreement. Confidence still derives from lead time,
completeness and vintage alignment. No MAE, RMSE or bias is published anywhere, in
the UI or over the API, and `verify_streamlit.py` asserts the Trust page keeps saying
so.

This paragraph has now been wrong twice as the work moved under it: it once read
"there is no verification fact", then "nothing yet separates the two". Both were true
when written. See findings O and P.

**Alerts do not send anything.** The evaluation engine works; there is no delivery
and no persistence. The page states this.

**The deployment can serve stale code, and now says so out loud.** On 2026-08-16 the
published app answered readers with an `ImportError` on two of nine pages for two and
a half hours while every gate above was green. Streamlit Community Cloud had failed
to pull the new commit eight consecutive times, reporting it only in a log, so the
container kept serving a checkout whose `dashboard/runtime.py` predated the symbols
the newer page modules import. A person found it by opening the app.
`scripts/verify_live_app.py` and the hourly `verify-live-app` workflow now watch the
deployment from outside; a reboot is still the remedy, and there is no API to
automate one.

**The deployment can also serve a stale warehouse.** `ensure_local_warehouse` returns
early whenever the file exists and a Community Cloud container keeps its disk, so a
container downloads the asset once and serves that copy until it is replaced, while
CI publishes a fresh one every six hours. Pipeline health now shows the served file's
age next to the published asset's, which makes the divergence visible; nothing
reloads automatically, and that was a decision rather than an omission.

**This is not production-ready.** Security, backup and observability have not been
addressed. The read API has no authentication, no rate limiting and no deployment
target; it runs locally against a warehouse and is not exposed anywhere.

## 6. Traps that cost time here

The four below were paid for in P6 and are the ones most likely to be met again.

**Three SQL Server instances share port 1433, and `.env` addresses the port.**
`MSSQLSERVER`, `SQLEXPRESS` and `SQLEXPRESS01` are all statically configured for 1433.
Whichever starts first binds it; the others die with *"Only one usage of each socket
address"* in the Windows **System** event log, not in any SQL Server log. Because the
connection is addressed by port rather than by instance name, starting the wrong one
silently points the whole pipeline at a server that has never held this database, and
the error names the login. `vn_air_quality` is on `SQLEXPRESS`.

**A `\v` in a Windows path can become a vertical tab, in a committed file.** Writing
`scripts\verify.ps1` through a layer that interprets backslash escapes produces
`scripts` + U+000B + `erify.ps1`. It renders almost correctly in a terminal, so it
survives review; it does not survive being copied and run. The README reached `HEAD`
with one such line in it. `git grep` will not find it by the text you expect, because
the text you expect is not there. Scan for the bytes:

```powershell
Select-String -Path *.md, docs\*.md -Pattern "[\x07\x08\x0b\x0c]"
```

Beware, too, of writing the scanner with `str.splitlines()`: Python treats vertical tab
and form feed as line boundaries, so the characters being hunted are consumed as
separators and the scan reports clean on exactly the corruption it exists to find. Use
`split("\n")`.

**sqlcmd substitutes scripting variables textually, before SQL Server parses.** A
password containing a single quote, spliced into `N'$(PASSWORD)'`, closes that literal
early and the remainder becomes SQL. `QUOTENAME` further down cannot help, because the
damage is done one layer above it. `create_serving_logins.ps1` passes the password
hex-encoded so that no character in transit can end a literal.

**A URL literal in JavaScript is right until the war is deployed.** `live.js` held
`new EventSource('/live/stream')`. Under `mvn spring-boot:run` the application owns the
root and that is correct; under Tomcat it is deployed at `/vn-air-web` and the request
404s. Nothing logs an error — from Tomcat's side a request for a path no application
claims is an ordinary 404 — and the only symptom is a live indicator that never leaves
"Đang kết nối…". Every test in that project started its own server at the root, where a
context-aware URL and a hard-coded one are the same string, so no test could tell them
apart. `ContextPathTests` now runs the application under a deliberately unrelated
context path; it fails 3/3 against the pre-fix code.

### Earlier traps, still true


Recorded so they do not cost it twice.

- **Streamlit caches imported modules.** Editing a library module and reloading the
  page shows the old code, and can show an `ImportError` for something that exists.
  Restart the server before believing a browser result.
- **Every deploy that adds a symbol to a library module needs a manual reboot.**
  Measured twice, on 2026-08-16 and again on 2026-08-17 within minutes of a push.
  Community Cloud re-executes page scripts on each run but keeps imported modules in
  `sys.modules`, so a page file that has just gained
  `from dashboard.components import source_registry` meets the *previous*
  `dashboard.components` and raises `ImportError`. Exactly the pages whose own import
  list changed will fail; every other page stays green, which is what makes the
  signature recognisable.
  **This also tells you the pull worked.** If the checkout had not advanced, the page
  file would still be the old one and would not reference the new symbol at all — so
  a page failing on a *new* import is proof the code arrived and the process did not
  restart. Reboot from **Manage app**; there is no API for it, and
  `verify_live_app.py` will keep failing until someone does.
- **A column added to an ingestion does not exist in warehouses already written.**
  The published asset is rebuilt every six hours from its own previous copy, so its
  `raw` tables keep the schema their rows were written with. Adding `station_latitude`
  to a staging model broke that workflow every cycle, and the error named neither the
  missing column nor the schema missing it -- DuckDB resolved the reference to the
  alias of the same name and reported `Binder Error: Column "station_latitude"
  referenced that exists in the SELECT clause`. Nothing local could see it: every
  fixture is regenerated from current code and always has the column, so the first
  environment to meet an older schema was production. `tests/test_legacy_raw_schema.py`
  is the missing step, and `COLUMNS_ADDED_AFTER_FIRST_LOAD` in it is the list the next
  such column belongs in.
- **Streamlit Community Cloud can fail to pull, and only whisper about it.** Eight
  consecutive `Updating the app files has failed: exit status 1` lines over two and a
  half hours, with no signal anywhere except the log behind **Manage app**. The
  container stays on the commit it cloned and serves it happily. Two independent
  tells: the redacted `ImportError` named a symbol that exists on `main`, and the
  dependency list in the boot log contained `dbt-core` and `dlt`, which a
  `pyproject.toml` from before `67ae666` installs and a later one does not. When the
  deployed app disagrees with `main`, read the boot log's install list before
  theorising — it dates the checkout.
- **A green offline gate says nothing about the deployment.** Every check in this
  repository proved something about code on a machine we control. None of them looked
  at the published app until `verify_live_app.py` existed. An HTTP 200 would not have
  helped either: Streamlit answers 200 with a shell document before any app code
  runs, so `curl` reports health while every page inside is failing.
- **FastAPI resolves string annotations against module globals.** With
  `from __future__ import annotations`, an `Annotated[..., Depends(...)]` alias
  defined inside an app factory is invisible to FastAPI: it cannot see the `Depends`,
  silently reclassifies the parameter as a query string, and every route answers 422
  instead of running. The alias must be at module scope.
- **A promise-checking test can fire on the promise.** The first version of the
  API's no-accuracy test swept the serialised payload for `mae` and failed on the
  disclosure sentence saying there is no MAE. Assert on field names, not on prose
  that quotes the thing being forbidden.
- **The Airflow image bakes `src/`.** `dags/` is bind-mounted, so DAG edits apply
  immediately while library edits do not. `../src` is now mounted too; without that
  the two drift silently and only the DagBag test notices.
- **Unpausing a DAG on a fresh metadata database schedules a run you did not ask
  for.** A six-hourly DAG has a current interval, so unpausing creates it at once,
  and that run takes the single slot in `warehouse_writer`. Anything triggered on
  top waits. The scheduling gate spent twenty minutes at `queued` on its first CI
  run and reported that the DAG had not executed — while the scheduler was busy
  executing the other run perfectly. A developer machine cannot show this: a DAG
  that has run for days has no missed interval to create. The gate now waits for the
  DAG to be idle, and if it still times out it names the run holding the pool.
- **`airflow dags test` is not evidence that anything can be scheduled.** It runs
  every task in-process, so it never touches the executor and never authenticates
  to the Task Execution API. Two settings were missing that made every scheduled
  task die with an empty log, and `dags test` reported 4/4 success throughout.
  `scripts/verify_airflow_scheduling.ps1` drives `dags trigger` instead, which is
  the real path. Finding M has the measurements. The general form: a check that
  runs the work in its own process cannot tell you the scheduler can run it.
- **A failing Airflow task can leave a log containing one line and no error.** If
  the supervisor dies during startup the task log holds only the buffered
  `Pre Execute` entry. The real traceback is in the **scheduler** container log,
  not the task log. Reach for `docker logs airflow-airflow-scheduler-1` before
  concluding there is no error to find.
- **A green test suite is not evidence the app looks right.** Four display defects
  passed 141 tests and ten page assertions. Every one needed a human eye.
- **Fixtures that cannot reach the broken state make tests pass vacuously.** Four
  fixes were initially unprovable for this reason. When adding a test, check it
  fails against the old behaviour before trusting it.
- **A frozen warehouse has a shelf life.** Any mart anchored to
  `current_timestamp` changes meaning as the query clock moves even when the file
  itself does not change; rows can age out until the result is empty. Any plan that
  relies on a prebuilt warehouse must answer how long that warehouse remains valid
  before it is treated as a usable data source.
- **PowerShell 5.1: do not pipe a native executable's `2>&1` output when `$?`
  matters.** PowerShell wraps each stderr line as `NativeCommandError` and sets
  `$?` to false even when the native exit code is 0. Streamlit writes warnings to
  stderr, so this made `verify.ps1` report a false failure twice in succession
  before the invocation pattern was identified as the cause.
- **A check that cannot pass on correct code is worse than no check.** Four have now
  been written here and corrected rather than shipped. The newest two: a clipped-text
  detector that included SVG `<text>` nodes, which do not use the box model and so
  reported every correct chart as broken; and `app.button[0]` in the history
  interaction check, which is the header's refresh control, not the form submit, so
  a healthy page was reported as broken.
- **A workaround can silently turn a failing run green.** `verify_layout.py` first
  collapsed the sidebar so Playwright would stop refusing clicks. That widened the
  main column, three clipped labels stopped being clipped, and the run went green
  while the defect was still on the page. The sidebar is now left as a reader finds
  it and the clicks go through JS instead. Ask what a workaround changes about the
  thing being measured.
- **`width="stretch"` overrides a pixel width in a single-view Vega spec, but not in
  a faceted one.** A facet keeps its declared width and overflows; that asymmetry is
  the whole reason the narrow-viewport defect existed and why the fix is per-panel
  charts rather than a smaller facet.

## 7. Where to pick up

In order:

1. **Decide whether the read API comes back.** P6 removed `api/main.py` and nothing
   replaced it: vn-air-web serves HTML and one status endpoint, so there is no
   machine-readable view of these marts at all. Anything that wants the numbers now
   needs a SQL Server credential, which is a worse boundary than the one that was
   removed. Rebuilding it in Spring is the obvious route; the contract it must keep is
   recorded in the pipeline README under *The user interface, and where it went*, and
   the rule that outranks the rest is that `mart_model_station_discrepancy` is never
   served and no accuracy figure is ever published.

2. **Re-ingest observed history so the decomposition has real numbers.** Unchanged by
   P6. The finding O split is built and all three terms are measured, but only on the
   CI fixture. Rows loaded before `b191358` carry no station coordinates and never
   will — backfilling them would mean rewriting immutable raw history to look like
   something it was not. One historical run gives future rows their positions, and it
   needs `OPENAQ_API_KEY`:

   ```powershell
   python -m vn_air_quality_weather.pipeline --date <a date already ingested>
   ```

   **The trap to carry forward:** the decomposition attributes the gap; it does not
   measure accuracy. Forecast drift near 1.0 means the forecast agrees with the model,
   not that the model is right, and a model offset by 3.9x produces a faithful forecast
   offset by 3.9x. The offset term is measured against one station per city in two
   cities and says nothing about the other thirty-two provinces.

3. **Give the three SQL Server instances distinct ports.** The port collision is
   documented rather than fixed, because two of those instances belong to other work on
   this machine. Until then, every deployment starts by checking which instance owns
   1433.

4. **Decide whether `sa` should be disabled.** Neither project uses it any more. It was
   left enabled deliberately: `NovaShopDb` and `webst2` share this instance and may
   still authenticate with it, so disabling it is a decision about other projects, not
   this one.

5. **The web application is not deployed anywhere but this machine.** It runs on a local
   Tomcat at `http://localhost:8080/vn-air-web/`. The public Streamlit deployment that
   used to exist was retired with the dashboard, so there is currently no public
   surface. Restoring one is a deployment task, not a revert.

Open findings and their status are in
[code-audit-and-risk-register.md](code-audit-and-risk-register.md), whose header now
says which of them describe a surface that no longer exists.
