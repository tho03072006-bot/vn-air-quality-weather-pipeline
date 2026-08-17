# Handover

State of the project as delivered. Written to be read by someone who was not here,
so it says what is verified, what is not, and what to do next — in that order.

Last commit at handover: see `git log -1`. Working tree clean, nothing staged.

## 1. What this is

An end-to-end pipeline and dashboard for Vietnamese air quality and weather:
hourly observations and modelled data for three cities, plus 72-hour modelled
forecasts for all 34 province-level units, served through a Streamlit decision
app. Local DuckDB warehouse, dbt for transformation, Airflow 3 for scheduling.

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
| Format | `ruff format --check .` | 95 files clean | 2026-08-17 |
| Lint | `ruff check .` | pass | 2026-08-17 |
| Unit tests | `pytest` | **333 passed, 1 skipped**, coverage 89.76% | 2026-08-17 |
| dbt | `dbt build` | **PASS=162, ERROR=0** | 2026-08-17 |
| Source freshness | `dbt source freshness` | pass | 2026-08-17 |
| Dashboard | `python scripts/verify_streamlit.py` | **32/32** — 9 fresh pages + interactions + 6 exhausted + 16 on an unreadable warehouse | 2026-08-17 |
| Layout | `verify_layout.py --skip-live-api` | **16/16** (8 pages x 2 viewports) | 2026-08-17 |
| Contrast | `verify_a11y.py --skip-live-api` | **16/16** (8 pages x 2 viewports) | 2026-08-17 |
| Keyboard | `verify_keyboard.py --skip-live-api` | **16/16**; 2.1.1, 2.4.7 and 2.4.3 enforced | 2026-08-17 |
| **Deployed app** | `python scripts/verify_live_app.py` | **9/9 pages** render their own heading, none raised | 2026-08-17 |
| Byte-compile | `compileall src dashboard api airflow/dags scripts` | exit 0 | 2026-08-17 |
| Compose | `docker compose -f airflow/docker-compose.yml config` | exit 0 | carried forward |
| Airflow DagBag | `airflow dags list-import-errors` | **0 import errors**, both DAGs registered | carried forward |
| Airflow pool | `airflow pools list` | `warehouse_writer` present, **1 slot** | carried forward |
| Airflow task code | `airflow dags test vn_air_quality_weather_forecast` | success, 4/4 tasks, 36.8s, 34/34 locations. **In-process — this does not exercise the scheduler**, see the row below and finding M | carried forward |
| **Airflow scheduled execution** | `verify-airflow-scheduling` workflow | **success, 4/4 tasks** via `dags trigger` — the executor and Task Execution API path, on a clean GitHub runner rather than a developer machine. Whole workflow 5m28s | 2026-08-17 |
| Whitespace | `git diff --check` | exit 0 | 2026-08-17 |
| Secret scan | `git grep` over tracked files | 2 known-benign hits | carried forward |

The three browser gates were run with `--skip-live-api`, which drops the
custom-location page because its driver enters coordinates and calls the live
forecast API. Eight gated pages, not nine; that page remains a manual check. The
earlier 18/18 figures in this table's history were taken without the flag and are
not comparable.

The Airflow rows are carried forward from the session that measured them. The stack
was not brought up again here, so treat them as last-known-good rather than as
current.

`.env` is not tracked; only `.env.example` is. The two secret-scan hits are the
deliberate fake `apikey=super-secret` in `tests/test_forecast_run.py`, which exists
precisely to prove `redact()` strips query strings from a persisted error summary.
Recorded here so a future scan does not read them as a leak.

Baseline when this work started: 82 tests, 86.40% coverage, 108 dbt checks, and a
dashboard script that only checked for exceptions.

## 3. How to run it

```powershell
cd "D:\VS Code\vn-air-quality-weather-pipeline"
& ".\.venv\Scripts\Activate.ps1"
python -m pip install --upgrade --editable ".[dev,etl]"
```

`etl` is required even for pytest: the suite imports `forecast_pipeline`,
`pipeline` and `duckdb_loader`, whose import chains need boto3 and dlt. Use a base
install with no extras only for the read-only dashboard runtime.

Add `api` to work on the read API — `".[dev,etl,api]"`, which is what CI installs.
Without it `tests/test_api.py` skips itself rather than failing, which keeps a
dev-only install green and would make those tests silently never run if CI omitted
the extra. Add `qa` for the browser gates, plus `python -m playwright install
chromium`.

Everything offline, in one command — no API call, no AWS call:

```powershell
.\scripts\verify.ps1
```

The dashboard, against the real warehouse:

```powershell
$env:DUCKDB_PATH = (Resolve-Path "data\warehouse\vn_air_quality_weather.duckdb").Path
streamlit run dashboard\app.py
```

Run Streamlit **from the project root**. Streamlit reads `.streamlit/config.toml`
relative to the working directory, so launching from elsewhere silently drops the
theme.

Refresh the data:

```powershell
python -m vn_air_quality_weather.forecast_pipeline --all-provinces
python -m vn_air_quality_weather.pipeline --date 2026-08-07
```

Both run `dbt build` at the end. The forecast run needs no API key; the historical
run reads `OPENAQ_API_KEY` from `.env`.

Airflow:

```powershell
docker compose -f airflow\docker-compose.yml build
docker compose -f airflow\docker-compose.yml up airflow-init
docker compose -f airflow\docker-compose.yml up -d
```

After every push that reaches the deployment:

```powershell
python scripts\verify_live_app.py
```

If it reports `ImportError` on exactly the pages whose import list changed, the code
arrived and the process did not restart — open **Manage app** on
<https://vn-air-quality-weather.streamlit.app/> and choose **Reboot app**, then run
the check again. This is a required step, not a troubleshooting one: see the first
trap in §6.

For a one-off daily-DAG test of data date `2026-08-07`, pass the following
interval end (not the data date) as the logical date:

```powershell
docker compose -f airflow\docker-compose.yml exec airflow-scheduler airflow dags test vn_air_quality_weather_daily 2026-08-08T02:00:00+00:00
```

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

**No accuracy figure is published, and a verification fact now exists.** Those are
two separate statements and the distinction is the whole of finding O.
`fct_forecast_verification` pairs each forecast hour with the observation that later
validated it, and `mart_model_station_discrepancy` publishes the resulting gap on the
Trust page. That gap is **not** forecast accuracy: it compares a CAMS grid cell with
one street-level station, so it mixes model error with representativeness error, and
nothing yet separates the two. Confidence remains derived from lead time,
completeness and vintage alignment. No MAE, RMSE or bias is published anywhere, in
the UI or over the API, and `verify_streamlit.py` asserts the Trust page keeps saying
so.

This paragraph previously read "there is no verification fact", which stopped being
true the moment one was built. See findings O and P.

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

1. **Finish the finding O separation — half of it is done.** Forecast drift is now
   measured against the model's own analysis at the same coordinate, needs no new
   data, and sits near 1.0 with no growth across lead bands: the published gap is not
   forecast error. What remains is splitting the rest into representativeness and
   model offset, which needs CAMS sampled at the station's own coordinates. The
   coordinates are already in 57 stored `openaq_locations` payloads, and
   `fetch_modeled_air_quality` takes a `City`-shaped value with `start_date`/`end_date`
   — so the existing paired window can be backfilled and no client change is needed.
   Until that split exists, no accuracy figure can be published honestly.
   **The trap to carry forward:** a drift near 1.0 is not accuracy. The forecast and
   the analysis are the same model twice, and a model offset by 3.9× produces a
   faithful forecast offset by 3.9×.
2. **Decide whether the deployment should reload its warehouse.** Pipeline health now
   shows that the served file and the published asset can diverge; nothing acts on it.
   The options and the constraints that must hold — `read_only=True`, atomic writes,
   and the load-bearing filename — are in the register under finding N's neighbours.
3. Single-source the duplicated promises. Finding P records the pattern: the same
   claim lives in `methodology.py` and in Trust's own container, and the gate now
   locks both copies without merging them.
4. Thin spots that remain in the gates: `verify_layout.py` covers no tablet width,
   the browser gates skip the custom-location page whenever `--skip-live-api` is set,
   and typography has never been checked by eye or by measurement.

Empty and error states are no longer on this list: `verify_streamlit.py` now drives
every warehouse-reading page against an absent warehouse and an empty one.

Open findings and their status are in
[code-audit-and-risk-register.md](code-audit-and-risk-register.md).
