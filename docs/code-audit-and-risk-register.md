# Code audit and risk register

Audit date: 2026-08-08. Audited tree: working tree on top of commit `9d554aa`
(52 uncommitted changes preserved, nothing staged).

## Verified baseline

Measured, not assumed:

| Gate | Command | Result |
|---|---|---|
| Format | `ruff format --check .` | 58 files already formatted |
| Lint | `ruff check .` | All checks passed |
| Unit tests | `pytest` | 82 passed |
| Coverage | `pytest --cov` | 86.40% (gate 70%) |
| Streamlit | `import streamlit` | 1.60.0 |

Lowest-coverage modules, which is where the correctness findings cluster:

| Module | Coverage |
|---|---|
| `forecast_pipeline.py` | **40%** |
| `open_aq.py` | 75% |
| `airflow_callbacks.py` | 83% |
| `geocoding.py` | 86% |
| `alerts.py` | 89% |

`forecast_pipeline.py` is both the newest orchestration surface and the least
covered one. Findings A, C and G below all live in it or in the models it feeds.

## Severity scale

- **P0** — produces wrong or misleading numbers in a user-facing surface, or
  loses data. Fix before any UI work.
- **P1** — correct today but structurally fragile, or a documented claim the
  code does not honour.
- **P2** — data-product limitation worth disclosing and scheduling.
- **P3** — cosmetic or cleanup.

---

## A. Mixed forecast vintage in the serving mart — P0

**Location:** `dbt/models/marts/mart_location_hourly_forecast.sql:3-9`, `:16`,
`:30-37`, `:74-76`

**Current behaviour.** Three independent "latest" selections are made and then
joined only on location and valid hour:

1. Lines 6-9 pick the newest row per `(location_key, valid_at_utc, pollutant)`.
   The partition includes `pollutant`, so **each pollutant independently
   resolves to its own newest vintage**.
2. Line 16 then collapses the surviving rows with
   `max(forecast_issued_at_utc)`, so the single issued-time the row reports is
   the newest contributing vintage.
3. Lines 33-36 pick the newest weather row per `(location_key, valid_at_utc)`,
   independently of whatever vintage air quality resolved to. Lines 74-76 join
   on `location_key` and `valid_at_utc` only — **vintage is not a join key**.

**Impact.** One serving row can carry PM2.5 from the 12:00 batch and O3 from
the 06:00 batch while displaying `forecast_issued_at_utc = 12:00`. The
`max()` actively hides the inconsistency instead of surfacing it. Because
`outdoor_score`, `decision_label` and `decision_explanation` (lines 95-115) are
all computed from these columns, a mixed-vintage row produces a recommendation
that corresponds to no actual forecast run. Weather/air mixing has the same
effect on `apparent_temperature_c` and `precipitation_probability_pct`.

This is not hypothetical: finding G means a partially failed run leaves exactly
this state — some pollutants refreshed, others not.

**Reproduce.** Load two forecast vintages for one anchor where the newer batch
is missing one pollutant, then select the serving row for a valid hour covered
by both. The row reports the newer issued time while serving the older
pollutant value.

**Fix.** Introduce an explicit `forecast_batch_id` on the forecast facts and
resolve the serving vintage **once per location**, not per pollutant and not
per weather series. Options, in order of preference:

1. Pick the newest batch per location that is *complete* for the pollutant set,
   then take all pollutants and weather from that batch only.
2. If air and weather genuinely come from different provider model runs, keep
   `air_forecast_issued_at_utc` and `weather_forecast_issued_at_utc` as
   separate published columns rather than one misleading `max()`.

Never publish a single `forecast_issued_at_utc` derived from `max()` across
rows that may disagree.

**Tests added.** `dbt/tests/assert_forecast_vintage_not_mixed.sql` asserts each
anchor serves exactly one air vintage and at most one weather vintage.

The offline fixture could not reproduce the defect as it stood: it emitted a
single vintage per anchor, so the buggy per-pollutant selection and the correct
per-anchor selection produced identical output and any vintage test passed
vacuously. `scripts/build_demo_warehouse.py` now emits two vintages six hours
apart, with the newer batch deliberately incomplete for two anchors — one loses
ozone, one loses its whole weather series.

Measured against that fixture, replaying the pre-fix selection yields **66
mixed-vintage served hours**; the fixed mart yields one air vintage and at most
one weather vintage per anchor, and flags the 72 rows whose weather comes from
an older run via `is_vintage_aligned = false`, which also degrades
`confidence_level` to LOW.

**Status:** done — `mart_location_hourly_forecast.sql` resolves the vintage once
per anchor, publishes `forecast_issued_at_utc` (air) and
`weather_forecast_issued_at_utc` separately instead of collapsing them with
`max()`, and exposes `is_vintage_aligned`.

Follow-up (not correctness): plumb an explicit `forecast_batch_id` / `run_id`
onto the forecast facts for traceability. The vintage timestamp already
identifies a batch uniquely because it is generated once per `run_forecast`
call, so this is deferred to item 1.3 where `run_id` is threaded through the
audit schema anyway.

---

## B. Flagged observations reach the official mart — P0

**Location:** `dbt/models/staging/stg_air_quality_measurements.sql` (flagged
passthrough), `dbt/models/marts/mart_city_air_quality_hourly.sql:9,11`

**Current behaviour.** Staging normalises `flagged` to a boolean and passes it
through with no filter. The mart then computes

```sql
avg(concentration) as concentration,
sum(case when flagged then 1 else 0 end) as flagged_measurement_count
```

`flagged_measurement_count` is *reported* but never *applied*. Flagged rows are
inside the `avg()`.

**Impact.** Provider-flagged measurements contribute to the concentration that
the mart publishes, and therefore to everything downstream of it including the
VN_AQI models and the dashboard. There is no explicit policy statement anywhere
saying flagged data is intentionally included, so the current behaviour reads as
an oversight rather than a decision. The offline fixture sets
`flagged = (hour == 23)` in `scripts/build_demo_warehouse.py`, so this path is
exercised on every build.

**Fix.** Make the policy explicit and enforce it:

- Exclude flagged rows from the official concentration aggregate.
- Keep them queryable — quarantine rather than drop, so the exclusion is
  auditable and reversible.
- Publish the excluded row count alongside the aggregate so a consumer can see
  how much was withheld.
- Write the policy into `docs/sources-and-limitations.md`.

**Tests added.** `dbt/tests/assert_flagged_measurements_excluded.sql` recomputes
the published concentration from the fact using unflagged rows only and fails on
any disagreement, and asserts every published row rests on at least one unflagged
reading.

The fixture again could not reproduce the case that matters. Its flagged rows
(`flagged = hour == 23`) were the *only* readings for their grain, so excluding
them merely made the grain disappear — measured: 12 flagged rows, 12 grains
withheld entirely, and **0 grains where the published value would have differed**.
A mart that averaged flagged readings back in would have looked identical. The
fixture now also carries a second, co-located Hanoi station reporting a suspect
PM2.5 value for one hour per day, giving that grain both a flagged and an
unflagged reading. Measured after: 15 flagged rows, 12 grains withheld entirely,
and **3 grains where including flagged readings would change the published
value**, each publishing `excluded_flagged_count = 1`.

**Status:** done. `mart_city_air_quality_hourly` averages unflagged readings
only, publishes `included_measurement_count` and `excluded_flagged_count`, and
drops grains with no unflagged reading rather than emitting a NULL concentration
that coverage would still count as data. Downstream that becomes a gap, which
`int_city_pollutant_hourly` already models correctly for the Nowcast window. The
withheld rows stay queryable in the new
`mart_flagged_measurement_quarantine` — quarantine rather than deletion, because
an exclusion policy that cannot be reviewed is indistinguishable from data loss.

`flagged_measurement_count` was renamed to `excluded_flagged_count`; it had no
consumers, and the old name described a count that documented an exclusion which
never actually happened.

---

## C. Forecast runs are never audited — P0

**Location:** `src/vn_air_quality_weather/forecast_pipeline.py:124-132`;
`src/vn_air_quality_weather/models.py:48-63`

**Current behaviour.** `run_forecast` calls `load_incremental(...)` **without
the `pipeline_runs=` argument**. Compare `pipeline.py`'s `run_day`, which does
pass it. No audit row is written for any forecast run.

`PipelineRunAudit` also cannot express a forecast run even if it were passed:

- No `pipeline_name` — historical and forecast rows would be
  indistinguishable.
- No `status`. There is no way to record RUNNING / SUCCESS / PARTIAL / FAILED.
- No per-location outcome counts.
- `data_date: date` is required and is part of the merge key
  (`primary_key=["run_id", "data_date"]`), but a forecast vintage spans 72
  hours and has no single data date.
- No error category or safe error summary.

**Impact.** Forecast freshness and success cannot be verified from the
warehouse. The Pipeline health page can only report on historical runs. Any
documentation claiming every run is audited is false for the forecast path.

**Fix.** Extend the audit model with `pipeline_name`, `status`, requested /
succeeded / failed location counts, raw objects attempted / created / reused,
and an error category plus a **redacted** summary. Give the fields defaults so
`run_day` keeps working unchanged. Reconsider the merge key: `run_id` alone,
or `run_id + pipeline_name`, since `data_date` is not meaningful for a forecast
vintage. Propagate through `stg_pipeline_runs` → `fct_pipeline_run`.

Error summaries must never contain the API key or a full URL with query
parameters.

**Tests added.** `tests/test_forecast_run.py` covers SUCCESS, PARTIAL and FAILED
outcomes, the created-vs-reused split, deterministic anchor order under
concurrency, and that a persisted error summary drops URL query strings. Plus
`dbt/tests/assert_pipeline_run_audit_consistent.sql`, which asserts
created + reused = attempted, succeeded + failed = requested, and that the
status agrees with those counts.

**Status:** done. `PipelineRunAudit` gained `pipeline_name`, `status`,
per-location counts, the created/reused split, forecast row counts, and a
redacted `error_category` / `error_summary`. All new fields carry defaults so
`run_day` kept its call shape. `run_forecast` now writes its audit row before
raising on a fully failed run, so a dead run is recorded *and* retried.

Two further defects were found and fixed while wiring this up:

- `fct_pipeline_run.sql` computed `is_latest_run_for_date` with
  `partition by data_date_utc` alone. Once forecast rows shared a date with
  historical rows, only one of the two pipelines could hold the latest flag, so
  the other silently vanished from the health panel. Now partitioned by
  `pipeline_name, data_date_utc`.
- The offline fixture set `raw_objects` but left the new created/reused fields
  at their 0 defaults, which violated the consistency invariant. The fixture now
  populates them, and its two forecast audit rows give the fixture a coherent
  account of its own shape: the older vintage covers only two anchors *because
  that run was PARTIAL*, which is exactly the state that made finding A
  reachable.

`data_date` is kept in the merge key and set to the issue date for forecast
runs. Changing the dlt primary key would force a migration on an existing
warehouse for no correctness gain.

**Status:** done

---

## D. Two DAGs can write DuckDB concurrently — P0

**Location:** `airflow/dags/vn_air_quality_weather_daily.py` (schedule
`0 2 * * *`, `catchup=True`), `airflow/dags/vn_air_quality_weather_forecast.py`
(schedule `0 */6 * * *`)

**Current behaviour.** `max_active_runs=1` is set on both DAGs, but that is
**per DAG**, not global. Both DAGs write the same DuckDB file and both call
`run_dbt_build` on it. There is no Airflow pool, no lock, and no single-writer
guard.

The schedules do not collide on the hour (02:00 vs 00/06/12/18), but they
overlap in practice: the daily DAG allows 45 minutes for extract plus 20 for
dbt, and it runs with `catchup=True`, so a backfill can put many daily runs
into the window where a forecast run is building analytics.

**Impact.** DuckDB is single-writer. A concurrent write fails the task, and two
overlapping `dbt build` runs can publish serving tables from interleaved state.

**Fix.** Serialise all warehouse writers. An Airflow pool with one slot,
applied to every task that writes DuckDB or runs dbt, is the smallest change
that actually holds. Add bounded retry for lock acquisition, and document the
constraint as an accepted limitation of the local DuckDB architecture rather
than pretending it scales.

**Tests added.** `tests/test_dag_structure.py` parses the DAG source with `ast`
rather than importing it, because Airflow is not a project dependency — it exists
only in the container image. It asserts every warehouse-writing task declares the
pool, that read-only tasks do *not* hold it (a one-slot pool held by work that
does not write would serialise the pipeline for nothing), that the pool is actually
provisioned by Compose (a task referencing a pool that was never created stays
queued forever, which reads as a hung scheduler rather than a config error), and
that neither DAG reads wall-clock time for its data window.

**Status:** done. `WAREHOUSE_WRITER_POOL` is defined once in `settings.py` so the
test and the DAGs cannot disagree, and `airflow-init` now runs
`airflow pools set warehouse_writer 1` after `db migrate`.

This is a static check, which is a real limitation: it verifies the declarations,
not that Airflow accepts them. The DagBag import test inside the Airflow image
covers the other half and has not been run in this environment.

---

## E. "Current conditions" is frozen at build time — P0

**Location:** `dbt/models/marts/mart_current_conditions.sql:4,11`;
`dbt/dbt_project.yml` (`marts: +materialized: table`)

**Current behaviour.** The model filters and ranks on `current_timestamp`:

```sql
where valid_at_utc >= date_trunc('hour', current_timestamp) - interval '1 hour'
...
order by abs(epoch(valid_at_utc - current_timestamp)), forecast_issued_at_utc desc
```

but marts are materialised as tables. `current_timestamp` is therefore
evaluated **once, at dbt build time**, and the result is frozen until the next
build.

**Impact.** Between builds the "current" row silently ages. Six hours after a
build it is pointing at an hour that is no longer current, with nothing in the
data saying so. The dashboard has no way to detect this because the row carries
no as-of marker.

**Fix.** Pick one and state it explicitly:

1. Materialise as a **view** so `current_timestamp` evaluates per query. Costs
   query time, always correct.
2. Keep the table but add an explicit `as_of_utc` column set at build time, and
   have the dashboard compute and display age from it, degrading loudly when
   stale.

Option 1 is preferable for a small local warehouse; option 2 is the honest
version if the table must stay materialised. Either way the dashboard must show
`data_as_of`, age, and a freshness status.

**A second model had the same defect.** `mart_outdoor_decision_window` also
anchored its 72-hour horizon on `current_timestamp` while materialised as a
table, so the ranked "better hours" on the Today page drifted into the past
between dbt runs and could recommend an hour that had already elapsed. Finding E
therefore covered two models, not one.

**Status:** done. Both models are now views, overriding the table default for
marts, so the clock is read per query. Both publish `as_of_utc` recording the
clock reading that produced the row. `mart_current_conditions` adds
`forecast_age_minutes`, a signed `data_age_minutes` (negative means the nearest
forecast hour is still ahead, which is normal because the selection takes the
closest hour rather than the last elapsed one) and a `freshness_status` of
FRESH / DELAYED / STALE.

The thresholds follow the six-hourly ingest cadence rather than round numbers:
FRESH up to 7 hours (one cycle plus slack), DELAYED to 13 hours, STALE beyond.
The 3-hour figure sketched in the UI spec would have flagged half of every normal
cycle as delayed.

**Verified by measurement**, not by inspection: querying the view twice two
seconds apart returns an `as_of_utc` that advanced, which a table could not do;
and `mart_outdoor_decision_window` reports **0 ranked hours earlier than the
current hour**, where a stale table would accumulate them.

The dashboard now renders this via `freshness_badge` in `dashboard/runtime.py`,
which states the age in words as well as colour, and the Today page shows
`as_of_utc` plus an explicit note when air and weather come from different model
runs.

**Tests added.** `schema.yml` asserts `mart_current_conditions.location_key` is
unique and not null, `as_of_utc` and `forecast_age_minutes` are not null, and
`freshness_status` is one of the three accepted values.

---

## F. Mixed date semantics across marts — P1

**Location:** `dbt/macros/vietnam_time.sql`; `mart_data_coverage.sql:5`;
all `cast(... as date)` sites

**Current behaviour.** A dedicated macro exists and is correct in isolation:

```sql
cast(timezone('Asia/Ho_Chi_Minh', <ts>) - interval '1 hour' as date)
```

The one-hour shift implements the VN_AQI daily convention (the index day runs
01:00 → 00:00 next day), and `dbt/tests/assert_vietnam_aqi_business_date.sql`
guards it. But `mart_data_coverage.sql:5` uses a different rule:

```sql
cast(observed_at_utc at time zone 'UTC' as date) as data_date_utc
```

**Impact.** Two different "day" definitions coexist in the mart layer. Coverage
is reported on UTC days while the AQI marts report on Vietnam business days.
A user comparing a coverage figure against an AQI figure for "the same day" is
comparing different seven-hour-offset windows. Neither is wrong on its own;
the inconsistency is the defect.

**The audit found it worse than described above.** Two further defects:

1. `mart_city_aqi_daily` computed the Vietnam business date with the correct macro
   but published it under the name **`data_date_utc`**, *and* republished the same
   value as `data_date_local`. A comment in the model admitted the `_utc` name was
   a misnomer kept for compatibility. That is the dangerous case: joining
   `mart_city_aqi_daily.data_date_utc` to `mart_data_coverage.data_date_utc`, a
   genuine UTC date, compared two windows seven hours apart and raised nothing.
2. `mart_city_air_quality_daily` used a **bare `cast(observed_at_utc as date)`**.
   Casting a TIMESTAMPTZ straight to DATE follows the DuckDB session TimeZone, so
   the same model produced a different day boundary depending on who ran it.

**Status:** done. `mart_city_aqi_daily` now publishes one column,
`data_date_vn` — one name for one meaning; `data_date_local` was ambiguous too
("local to what?"). `mart_city_air_quality_daily` uses an explicit
`at time zone 'UTC'`. Consumers updated: `schema.yml`,
`assert_vn_aqi_within_scale.sql`, and `dashboard/data_access.py`.

**Test added.** `assert_business_date_naming_is_honest.sql` recomputes every
`_utc`-named date column from its own timestamp and fails on disagreement, so a
column that drifts back onto a local calendar breaks the build rather than quietly
disagreeing with its neighbours.

---

## G. Forecast ingestion is all-or-nothing — P0

**Location:** `src/vn_air_quality_weather/forecast_pipeline.py:70-122`

**Current behaviour.** A plain `for province in provinces:` loop issues two API
calls per province with no per-location error handling. `load_incremental` is
called only after the loop completes (line 124).

**Impact.** For a 34-province run that is 68 sequential requests. If province 20
raises — one timeout, one rate-limit that outlives the retry policy — the
exception propagates, the loop aborts, and **nothing at all is loaded**, not
even the 19 provinces that succeeded. Work is discarded and there is no record
of which location failed.

There is also no bounded concurrency, so the run is slow, and no per-location
resume path.

**Related defect, same file, line 122.** `raw_objects += 1` is unconditional,
but `RawJsonStore.write()` already returns `RawWriteResult` with a
`created: bool` field (`storage/raw_json.py:57-58, 68, 97-98, 107`). Both this
pipeline and `pipeline.py`'s `run_day` **discard that return value** and count
every write as a new object. A reused object is reported as created.

**Fix.**

- Wrap each location in per-location error capture; record outcome per
  location; continue the loop.
- Load whatever succeeded, and record the run as PARTIAL (see finding C).
- Add bounded concurrency sized to the provider rate limit, with retry and
  backoff.
- Provide a resume path that reprocesses only failed locations.
- Consume `RawWriteResult.created` and count attempted / created / reused
  separately, in both pipelines.

**Tests added.** See `tests/test_forecast_run.py`. A run where one anchor raises
still loads the others and reports PARTIAL naming the failed anchor; writing the
same payload twice reports created then reused.

**Status:** done. Each anchor is wrapped in per-anchor error capture and returns
a `LocationOutcome`, so one timeout costs one anchor instead of the whole run.
Bounded concurrency comes from `Settings.forecast_max_workers` (default 4, kept
low because Open-Meteo's free tier is rate limited and a burst turns a healthy
run into a partial one); `pool.map` preserves submission order so the loaded
record order stays deterministic. Both pipelines now consume
`RawWriteResult.created` — `pipeline.py` via a small `_RawObjectCounts` helper.

The resume path is the existing `--province` flag rather than new
infrastructure: the audit row and the CLI both name the failed anchors, and the
CLI prints a ready-to-paste rerun command. That is a deliberate scope choice, not
an oversight — a queue would add moving parts for a case the flag already covers.

**Status:** done

---

## Data-product limitations to disclose, not silently fix — P2

These are honest modelling limits. They must be visible in the UI and in
`docs/sources-and-limitations.md`, not papered over.

| # | Limitation | Where it misleads |
|---|---|---|
| 1 | One anchor point represents an entire province | Any province-level claim implies uniform coverage it does not have |
| 2 | Anchors are model grid points, not stations | Must never be labelled "station" |
| 3 | `forecast_issued_at_utc` is **fetch time**, not provider model-run time (`forecast_pipeline.py:55`) | "Issued at" reads as provider authority it does not have |
| 4 | `confidence_level` derives only from lead time and null-ness (`mart_location_hourly_forecast.sql:99-103`) | Cannot be called accuracy — there is no verification fact yet |
| 5 | `outdoor_score` is a transparent heuristic (`:82-98`) | Must never be presented as VN_AQI or as health advice |
| 6 | "Best windows" are ranked individual hours, not contiguous blocks | A user reads a window as a continuous block of time |
| 7 | OpenAQ sensor selection is narrow; no station registry or reconciliation | Coverage looks more complete than it is |
| 8 | Observed/modeled fusion for current conditions is incomplete | Source of a displayed number can be ambiguous |
| 9 | Alerts are evaluation-only; no delivery, no persistence | UI must say preview-only |
| 10 | Custom locations are not persisted | No saved places |
| 11 | No forecast accuracy mart | No empirical error figures exist |

---

## Roadmap candidates (not started)

Ordered by dependency, not by appeal:

1. Station / provider / license dimension — prerequisite for honest observed
   coverage.
2. Observed-recent → observed-delayed → modeled fallback with an explicit
   per-row source label.
3. Forecast verification fact joining each vintage to the observation that
   later validated it.
4. MAE / RMSE / bias by location, pollutant and lead hour, from that fact.
5. Empirical confidence derived from 4, replacing the current heuristic.
6. Contiguous 2h / 3h outdoor windows.
7. User activity profile and sensitive-group preference.
8. Saved locations.
9. Alert delivery with history and status.
10. Source and data-quality marts.

Items 3-5 are the only path to calling any number "accuracy". Until then the
UI must keep saying confidence.
