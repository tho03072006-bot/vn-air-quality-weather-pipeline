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
| Unit tests | `pytest` | **300 passed**, coverage 89.76% |
| Contrast | `python scripts/verify_a11y.py` | **18/18** (9 pages x 2 viewports) |
| Keyboard | `python scripts/verify_keyboard.py` | **no blocking findings**; 23 advisory (2.4.3) |
| dbt | `dbt build` | **PASS=129, ERROR=0** |
| Source freshness | `dbt source freshness` | pass |
| Dashboard | `python scripts/verify_streamlit.py` | **10/10** (9 pages + interactions) |
| Layout | `python scripts/verify_layout.py` | **18/18** (9 pages x 2 viewports) |
| Byte-compile | `compileall src dashboard airflow/dags scripts` | exit 0 |
| Compose | `docker compose -f airflow/docker-compose.yml config` | exit 0 |
| Airflow DagBag | `airflow dags list-import-errors` | **0 import errors**, both DAGs registered and paused |
| Airflow pool | `airflow pools list` | `warehouse_writer` present, **1 slot** |
| **Airflow end-to-end** | `airflow dags test vn_air_quality_weather_forecast` | **success, 4/4 tasks, 36.8s**, 34/34 locations, dbt PASS=129 |
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

Install with `pip install -e ".[qa]"` then `python -m playwright install chromium`.
The `qa` extra is kept out of `dev` so `dev` stays installable offline.

**Screenshots still do not exist.** The Browser pane does not composite frames in
this environment, so `computer{action:"screenshot"}` times out and nothing has been
seen as an image. This turned out to matter less than it appeared: both defect
classes are geometry, and geometry is measurable. Contrast ratios and typography
remain unverified by eye.

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

1. [implementation-roadmap.md](implementation-roadmap.md) Phase 6 — the production
   roadmap, with a table of what must be decided before each item can be built
   honestly. Items 3–5 there are the only route to publishing an accuracy figure.
2. Extend `verify_layout.py` where it is still thin: it measures two viewports and
   the default filter state only. The stale, empty and error states of each page are
   still unmeasured, and a tablet width is not covered.
3. Accessibility remains the largest unverified area: contrast ratios and typography
   have never been checked, by eye or by measurement. Both are measurable from the
   DOM the same way the layout checks are, so this is now a smaller job than it was.

Open findings and their status are in
[code-audit-and-risk-register.md](code-audit-and-risk-register.md).
