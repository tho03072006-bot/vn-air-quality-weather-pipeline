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

Every figure below was measured at handover, not estimated.

| Gate | Command | Result |
|---|---|---|
| Format | `ruff format --check .` | 73 files clean |
| Lint | `ruff check .` | pass |
| Unit tests | `pytest` | **147 passed**, coverage 89.59% |
| dbt | `dbt build` | **PASS=129, ERROR=0** |
| Source freshness | `dbt source freshness` | pass |
| Dashboard | `python scripts/verify_streamlit.py` | **10/10** (9 pages + interactions) |
| Byte-compile | `compileall src dashboard airflow/dags scripts` | exit 0 |
| Compose | `docker compose -f airflow/docker-compose.yml config` | exit 0 |
| Airflow DagBag | in-container import test | **0 import errors**, pool assignments confirmed |
| Whitespace | `git diff --check` | exit 0 |
| Secret scan | `git grep` over tracked files | 2 known-benign hits |

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
```

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

## 5. Not verified — read this before trusting anything

**Two pages have never been seen in their loaded state.** Both are behind an
interaction and both are code written during this work:

- History's coverage strip renders only after the filter form is submitted.
- Custom location's charts render only after a place is chosen, which calls an API.

**Two defect classes have no automated guard.** `verify_streamlit.py` runs on
AppTest, which has no DOM and no layout engine, so it cannot see:

- values truncated by CSS ellipsis;
- a chart wider than its container.

Both were found by eye and both recurred more than once. Closing this needs a
browser-driving QA script — Playwright or similar — which is a dependency decision
left open deliberately rather than added unverified.

**Performance is measured server-side only.** The numbers in
[ui-design-spec.md](ui-design-spec.md) exclude browser paint, WebGL setup for the
map, and client-side Altair rendering.

**No accuracy figure exists.** Confidence is derived from lead time, completeness
and vintage alignment. There is no verification fact, so no MAE, RMSE or bias is
published anywhere, and `verify_streamlit.py` asserts the Trust page keeps saying
so.

**Alerts do not send anything.** The evaluation engine works; there is no delivery
and no persistence. The page states this.

**This is not production-ready.** Deployment, security, backup and observability
have not been addressed.

## 6. Traps that cost time here

Recorded so they do not cost it twice.

- **Streamlit caches imported modules.** Editing a library module and reloading the
  page shows the old code, and can show an `ImportError` for something that exists.
  Restart the server before believing a browser result.
- **The Airflow image bakes `src/`.** `dags/` is bind-mounted, so DAG edits apply
  immediately while library edits do not. `../src` is now mounted too; without that
  the two drift silently and only the DagBag test notices.
- **A green test suite is not evidence the app looks right.** Four display defects
  passed 141 tests and ten page assertions. Every one needed a human eye.
- **Fixtures that cannot reach the broken state make tests pass vacuously.** Three
  fixes were initially unprovable for this reason. When adding a test, check it
  fails against the old behaviour before trusting it.
- **A check that cannot pass on correct code is worse than no check.** Two were
  written here and corrected rather than shipped.

## 7. Where to pick up

In order:

1. Drive the two unverified interaction states and look at them.
2. Decide on a browser QA dependency; if yes, port the four-class sweep in
   §5 into it.
3. [implementation-roadmap.md](implementation-roadmap.md) Phase 6 — the production
   roadmap, with a table of what must be decided before each item can be built
   honestly. Items 3–5 there are the only route to publishing an accuracy figure.

Open findings and their status are in
[code-audit-and-risk-register.md](code-audit-and-risk-register.md).
