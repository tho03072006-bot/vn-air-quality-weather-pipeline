# Implementation roadmap

Companion to [code-audit-and-risk-register.md](code-audit-and-risk-register.md)
and [ui-design-spec.md](ui-design-spec.md). Status values: planned /
in-progress / done / deferred.

Baseline at time of writing (measured, commit `9d554aa` + 52 uncommitted
changes): ruff pass, 82 pytest pass, coverage 86.40%, Streamlit 1.60.0.

## Sequencing rationale

Phase 1 comes before any UI work because three of the P0 findings change the
shape of the serving data the UI reads. Redesigning pages against
`mart_location_hourly_forecast` before fixing its vintage logic would mean
building components around columns that are about to change meaning.

Within Phase 1, finding G (partial failure) is done alongside C (run audit)
because the audit schema is what records the partial outcome — splitting them
would mean writing the audit twice.

## Phase 1 — P0 correctness

| # | Item | Touches | Status |
|---|---|---|---|
| 1.1 | Single forecast vintage per serving row; separate air/weather issued-at; `is_vintage_aligned` | `mart_location_hourly_forecast.sql`, `assert_forecast_vintage_not_mixed.sql`, `build_demo_warehouse.py` | **done** |
| 1.2 | Flagged-observation policy: exclude from official mart, quarantine, publish excluded count | `mart_city_air_quality_hourly.sql`, `mart_flagged_measurement_quarantine.sql`, `assert_flagged_measurements_excluded.sql`, `schema.yml`, `build_demo_warehouse.py` | **done** |
| 1.3 | Forecast run audit with status and per-location counts; `is_latest_run_for_date` partitioned by pipeline | `models.py`, `forecast_pipeline.py`, `stg_pipeline_runs.sql`, `fct_pipeline_run.sql`, `schema.yml`, `assert_pipeline_run_audit_consistent.sql`, `data_access.py` | **done** |
| 1.4 | Per-location partial success, bounded concurrency, resume via `--province` | `forecast_pipeline.py`, `settings.py`, `vn_air_quality_weather_forecast.py`, `tests/test_forecast_run.py` | **done** |
| 1.5 | Count raw objects attempted / created / reused via `RawWriteResult.created` | `forecast_pipeline.py`, `pipeline.py`, `build_demo_warehouse.py` | **done** |
| 1.6 | Single-writer policy for DuckDB via a one-slot Airflow pool | `settings.py`, both DAGs, `docker-compose.yml`, `tests/test_dag_structure.py` | **done** |
| 1.7 | Current-conditions freshness: views with `as_of_utc` + `freshness_status`; same fix applied to `mart_outdoor_decision_window` | `mart_current_conditions.sql`, `mart_outdoor_decision_window.sql`, `schema.yml`, `runtime.py`, `today.py` | **done** |
| 1.8 | Reconcile date semantics; `_vn` vs `_utc` naming | `mart_city_aqi_daily.sql`, `mart_city_air_quality_daily.sql`, `schema.yml`, `assert_business_date_naming_is_honest.sql`, `data_access.py` | **done** |

**Phase 1 complete.** Measured at the end of it: ruff clean, 123 pytest passed,
coverage 89.59% (up from the 86.40% baseline), `dbt build` PASS=129 ERROR=0,
`dbt source freshness` clean, 9/9 Streamlit pages pass, `compileall` clean,
`git diff --check` clean, nothing staged.

Two defects outside the original eight were found while implementing them and
fixed: `is_latest_run_for_date` was partitioned by date alone so historical and
forecast runs competed for one latest flag (1.3), and `mart_outdoor_decision_window`
shared the frozen-clock defect with `mart_current_conditions` (1.7). One test
robustness bug was also fixed: `test_default_duckdb_path` failed for anyone who had
exported `DUCKDB_PATH`, which the README instructs the reader to do.

A recurring lesson worth recording: **three of the eight fixes were initially
unprovable** because the offline fixture could not reach the broken state. The
fixture emitted one forecast vintage per anchor (so mixed-vintage tests passed
vacuously) and its flagged readings were always the only reading for their grain
(so exclusion only made grains vanish, never moved a published number). Each fix
therefore included extending the fixture and measuring that the pre-fix logic
actually failed against it.

## Phase 2 — Dashboard architecture

| # | Item | Status |
|---|---|---|
| 2.1 | `dashboard/components/` package: `metric_cards`, `methodology`, `provenance` | **done** |
| 2.2 | `dashboard/view_models.py` with no Streamlit import, 27 unit tests | **done** |
| 2.3 | Split `runtime.py`: caching stays, rendering moved, labels de-duplicated | **done** |
| 2.4 | Null-safe `metric_cards` (fixes G2) | **done** |
| 2.5 | `app_header` with primary location, freshness badge, refresh popover | **done** |
| 2.6 | Methodology into one expander; `render_modeled_disclaimer` now delegates | **done** |
| 2.7 | Shared pollutant colour dictionary and Altair chart specs | planned |

`view_models.py` earns its separation immediately: writing tests against it found a
real bug in the missing-value check. `pandas.NA` propagates through `value != value`
and then refuses `bool()`, so the first implementation caught that `TypeError` and
reported NA as *present*. A page formatting an NA column would still have printed
"nan". That defect was invisible from the Streamlit side and obvious from a unit
test — which is the argument for the layer.

Note on coverage: `pyproject.toml` scopes `--cov` to `vn_air_quality_weather`, so
the 27 `view_models` tests raise the test count but not the reported percentage.
The dashboard package is not in the coverage denominator at all.

**2.5 as built, and why it is not what the spec asked for.** The spec called for one
global location selector "reused by every page". That is not achievable: `compare.py`
selects three to five locations at once and `custom_location.py` takes arbitrary
coordinates, so no single selector serves all nine pages. The header therefore owns
the **primary** location — the one the single-location pages read through
`st.session_state` — and says so in its help text; those two pages keep their own
control. The spec text was aspirational rather than checked against the pages.

The header renders in `app.py` before `navigation.run()`, so it appears once per page
instead of being repeated in nine scripts. It degrades rather than stops when the
warehouse or `dim_province` is missing, because the Trust and Pipeline health pages
are needed precisely when the warehouse is broken — stopping the app from inside the
header would hide the pages that explain why.

The refresh control clears only `cached_current`. Clearing every cache would also
discard the geocoding and on-demand forecast results, which cost real API calls to
rebuild.

## Phase 3 — High-value pages

| # | Item | Status |
|---|---|---|
| 3.1 | Today: hero recommendation, ≤4 null-safe KPIs, 24h PM2.5 timeline with threshold annotation | **done** (sensitive-group advisory outstanding) |
| 3.2 | Forecast: 24/48/72 segmented control, small multiples, separate weather/rain/UV panels, vintage disclosure | **done** |
| 2.7 | Shared pollutant colour dictionary and Altair specs (landed with 3.2) | **done** |
| 3.3 | National map: `st.pydeck_chart` replacing `st.map`, tooltip, selection, legend, metric switch, ranking table, accessible table | **done** |

3.2 depended on 1.1. 3.3 depended on 2.1.

**Two chart defects the redesign fixed**, both in the old `forecast.py`:

1. PM2.5, PM10, NO₂ and O₃ shared one y axis. They share a unit but not a range —
   O₃ runs around 40–90 µg/m³ while NO₂ sits near 5–15 — so NO₂ flattened onto the
   baseline and the shape the chart existed to show was gone. Now one faceted panel
   per pollutant with `resolve_scale(y="independent")`, and a caption telling the
   reader not to compare heights across panels, because independent scales make that
   comparison meaningless.
2. `precipitation_probability_pct` (0–100 %) shared an axis with `uv_index` (0–11).
   Different unit *and* different range: one curve was decorative, the other
   unreadable. They are now separate panels.

`forecast.py` also stated the vintage as `forecast['forecast_issued_at_utc'].max()`
— the same misleading `max()` that finding A removed from the mart. It now prints the
air and weather vintages separately and says so explicitly when they differ.

**2.7 as built.** `POLLUTANT_COLOURS` lives in `view_models` and every chart reads
it, so a pollutant is the same colour on every page. A pollutant that is blue on one
page and orange on the next makes two charts impossible to compare at a glance.

**Assertions, not just absence of exceptions.** `verify_streamlit.py` now requires
the strings that carry the design promises: `"trục y độc lập"` and
`"không so sánh độ cao giữa các khung"` on the forecast page (if the panels were
collapsed back onto one axis those captions would be lies), `"Chỉ số UV"` plus
`"không cùng thang"`, `"Lần chạy mô hình"`, and on Today the 24-hour timeline
heading plus `"ngưỡng nồng độ, không phải giá trị VN_AQI"`. Altair rendering itself
is still unverified — AppTest does not inspect chart specs, and no browser QA has
been run.

**Outstanding in 3.1:** the sensitive-group advisory. It needs a decision on where
the group definition comes from (a user preference, per Phase 6 item 7) rather than
being hardcoded, so it is deferred rather than guessed at.

**3.3 as built.** `ScatterplotLayer` with a stable `id="province-anchors"`,
`pickable`, `auto_highlight`, `map_style=None` so the basemap follows the theme,
`width="stretch"`, and `on_select="rerun"` driving a detail panel. Metric switch via
`st.segmented_control` across PM2.5 / outdoor score / temperature / rain.

The colour bands live in `view_models` as data, not branching, so the legend and the
marker fills are read from one source — a map whose key is written separately from
its fills drifts the first time a threshold moves and the reader cannot tell. 18
tests cover the banding: threshold boundaries against their printed ranges, missing
anchors rendering grey rather than as the low end of the ramp (which would read as
clean air), `MISSING_RGB` not colliding with any band, and the outdoor-score bands
agreeing with the `decision_label` cut points in
`mart_location_hourly_forecast` — without that last one a row can show a green
marker beside the words "Nên hạn chế".

Marker radius is banded rather than proportional. Sizing by raw value let one
polluted anchor swamp the map and gave every anchor a different radius for
differences too small to mean anything.

PM2.5 uses the Bang 2 concentration breakpoints of QĐ 1459/QĐ-TCMT, and the legend
says so explicitly: these are µg/m³ cut points, not the AQI index value. Colouring a
concentration with familiar AQI colours otherwise invites being read as the official
index.

One anchor per province stays a point. No polygon fill: an area fill would assert
uniform provincial coverage the data cannot support.

## Phase 4 — Remaining pages

| # | Item | Status |
|---|---|---|
| 4.1 | Compare: 3–5 locations, ranking table, activity priority, per-metric panels | **done** |
| 4.2 | History: coverage strip, missing-data table, explicit local time, null-safe KPIs | **done** |
| 4.3 | Custom location: null-safe KPIs, pollutant small multiples, shared methodology | **done** |
| 4.4 | Alerts: explicit preview-only labelling | **done** |
| 4.5 | Trust: methodology, sources, freshness, limitations, VN_AQI legal basis | **done** |
| 4.6 | Pipeline health: status counts, last success, latest incomplete run, created/reused split, forecast runs included | **done** |

**4.4 corrected a false claim in the UI, not just a missing label.** The page said
"Delivery Telegram cần cấu hình `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` trong
`.env`", which reads as "configure these two variables and alerts will be sent".
Grepping `src/vn_air_quality_weather/alerts.py` for `httpx`, `send`, `post` and
`telegram` returns **nothing** — there is no delivery code and no persistence at all,
and nothing reads those settings to send anything. The page now states plainly that
no message is sent, no rule is saved, and that configuring those variables currently
does nothing. `verify_streamlit.py` asserts that wording stays.

**4.6 surfaces what 1.3 made available.** Status counts across SUCCESS / PARTIAL /
FAILED, the last fully successful run, and the most recent incomplete one with its
succeeded/requested location counts and its redacted `error_summary`. PARTIAL is the
outcome most worth showing: nothing errored, so nobody is paged, yet the warehouse is
incomplete. The raw created/reused split is displayed with an explanation, because
"attempted 68, created 2" looks like a bug until you know the raw layer is
content-addressed.

**4.1 declined to invent a score.** The spec asked for a filter by purpose (running,
walking, tourism, events). Implemented as an explicit **sort order over columns that
already exist**, not a per-activity index: "ranked by PM2.5, then apparent
temperature" is a claim the data supports, while "your running score is 72" would be
a fifth unvalidated heuristic stacked on `outdoor_score`. The rule is printed above
the table, because a ranking whose rule is invisible is just an assertion.
`ACTIVITY_PRIORITIES` lives in `view_models`. Missing values sort last under either
direction, so a location with no reading cannot win the ranking by default.

Four metrics get four panels with their own axes rather than one chart, and a caption
says not to compare bar lengths across them.

**4.2 added the coverage strip** — hours with data out of 24 per UTC day, plus a table
of the incomplete days — and now shows local time on the trend axis while keeping the
UTC filter labelled as UTC. Leaving only one of the two clocks on a page where the
filter and the axis disagree by seven hours makes a misreading close to inevitable.
The methodology expander carries two history-specific notes, including that flagged
readings were excluded and where to find them.

**4.3 fixed the same two defects the other pages had.** `custom_location.py` still
formatted `f"{current['pm25_ugm3']:.1f}"` inline (the G2 "nan µg/m³" bug) and still
put four pollutants on one shared axis (the 3.2 defect). An on-demand fetch is at
least as likely to return a null pollutant as the warehouse is. Both now use the
shared components.

**4.5 reads the warehouse rather than describing it.** A trust page that states its
own coverage in prose is the first thing to go stale, so the coverage count, the
count of anchors whose air and weather vintages disagree, the oldest fetch age and
the FRESH/DELAYED/STALE distribution are all measured live. When any anchor is
serving mixed vintages the page says how many and that their confidence was degraded.

Limitations are listed flat rather than softened, because the failure this page exists
to prevent is a reader over-trusting a modelled anchor: one grid point does not
represent a province, an anchor is not a station, `forecast_issued_at_utc` is fetch
time and not the provider's model run, `outdoor_score` is not VN_AQI, the ranked hours
are not a contiguous window, history covers three cities, and Đà Nẵng has no OpenAQ
station at all.

It also states outright that **no accuracy figure is published** — no MAE, no RMSE,
no bias — and what would have to be built first. `verify_streamlit.py` asserts that
sentence stays, so a future change cannot quietly start claiming accuracy.

**Phase 4 complete.** Assertions were also backfilled for `compare.py` and
`history.py`, which had been passing on exception-absence alone. History renders
behind a form submit, so only its pre-submit prompt and its source-separation promise
are asserted; driving the form belongs with the outstanding interaction work in 5.1.

## Phase 5 — Quality

| # | Item | Status |
|---|---|---|
| 5.1 | AppTest content assertions | **done** — fresh and exhausted warehouses; history form driven too |
| 5.2 | Accessibility pass: contrast, keyboard, colour-independence, table equivalents | **done** — contrast 18/18; keyboard 2.1.1 and 2.4.7 enforced and passing, 2.4.3 advisory |
| 5.3 | Performance measurement, then fill the UI spec table with real numbers | **done** |
| 5.4 | Browser QA | **done** — automated in `scripts/verify_layout.py`; still no screenshots |
| 5.5 | README and docs reconciled against actual behaviour | **done** |

**5.1 as far as it goes.** `scripts/verify_streamlit.py` previously asserted only
`app.exception` — it passed for a page that silently rendered an empty state or lost
its provenance badges. Each page now declares required text, required widget labels,
a minimum number of data tables, and forbidden strings (`"nan µg/m³"` and friends, so
the G2 formatting bug cannot come back unnoticed).

**5.1 now has a second warehouse branch.** The original fresh path remains 9 pages
plus interactions. The second path builds a fixture with
`--forecast-age-hours 96` and exercises Today, National map, Forecast, Alerts, Trust
and Compare after the forecast horizon is exhausted. Each page must state the
exhausted condition, vintage and age; reader-facing output may not expose
`python -m vn_air_quality_weather` or `dbt build`; and National map must retain its
accessible table while rendering no coloured PyDeck markers. Together the two paths
report 16/16. Three mutations independently proved the operational-command guard,
the map-marker guard and Today's exhausted-state guard fail only the page whose
protection was removed, while the fresh path remains PASS.

Writing those assertions immediately caught two faults in the checker itself:
`_widget_labels` omitted `segmented_control` and `pills`, so it reported a missing
filter on a page that had one; and an expander-label assertion failed against working
code because AppTest 1.60 reports zero elements in `app.expander` for these pages.
The second was removed rather than weakened — a check that cannot pass on correct
code is worse than no check — and the expander's body caveats are asserted directly
instead, which is the part that has to be present anyway.

**5.1 interaction assertions now exist.** `_check_interactions` drives the forecast
horizon from 72 to 24 and fails unless the hour count actually shrinks, and switches
the map metric to the outdoor score and fails unless the legend bands change — it
asserts both that the new bands appear *and* that the PM2.5 bands are gone, so a
legend that renders both would fail. Both checks are discriminating by construction: a
control that renders but does not re-query leaves the counts equal and the old bands
present.

**5.1 now drives the history form.** `_check_history_form` submits it and asserts the
loaded state rather than the pre-submit prompt. Driving it found two defects nothing
else could have: the pollutant defaulted to `pollutants[0]`, which is NO2
alphabetically and has *zero* observed rows in the warehouse, so a first-time
reader's default submit could only ever return "Không có dữ liệu phù hợp" and read as
a broken pipeline; and the coverage strip grouped by day, so a day with no rows
produced no group and the page printed "every day in the selected range has a full 24
hours" while ten of the twelve days held nothing at all.

The second is the more serious, because the strip exists precisely to show missing
data and was structurally blind to the worst case it could report. `build_coverage`
now reindexes over the selected range times the selected locations, so an absent day
is an explicit zero. Six tests cover it in `tests/test_coverage_view.py`, and three
of them were confirmed to fail against the old implementation before being trusted —
the old code returned 2 rows where the fixed code returns 12.

Writing the check also produced a third instance of the recurring lesson: the first
version clicked `app.button[0]`, which is the header's "Đọc lại warehouse" refresh
control rather than the form submit, and reported a healthy page as broken. The
*check* was fixed, not the page.

Still missing from 5.1: empty and error states, and an assertion that no network call
happens on initial render. The stale/exhausted state is now measured by the second
warehouse branch.

**5.4 is now automated.** `scripts/verify_layout.py` drives Chromium via Playwright
across nine pages at 390x844 and 1280x800, measuring chart-versus-container width and
CSS-clipped text — the two classes AppTest structurally cannot see. Playwright lives
in a `qa` extra rather than `dev`, because it needs a browser download and `dev` must
stay installable offline.

It found the facet grid overflowing again. The Phase 3.2 fix sized it for a 790px
desktop column; at a 390px viewport the same grid measured 641px inside a 327px
container, and because no ancestor scrolled, three of the six pollutant panels were
unreachable rather than merely cramped. The cause is that a Vega-Lite **facet** keeps
its declared pixel width, while `width="stretch"` does override a pixel width on a
*single* view — so the fix is one chart per pollutant laid out in column pairs, with
no pixel width anywhere. Independent y scales, the point of the original redesign,
are now structural rather than a `resolve_scale` argument.

It also found four KPI **labels** clipped at 1280px. Those are a direct consequence
of the Phase 3.1 fix: the unit was moved out of the metric value and into the label
to stop the value being truncated to `75…`, justified by "the label wraps". The label
does not wrap — Streamlit gives it `white-space: nowrap` and an ellipsis too — so the
fix relocated the defect instead of removing it, and "PM2.5 mô hình trung vị (µg/m³)"
needed 183px in a 161px box. `app.py` now overrides that CSS so labels wrap, which
closes the class rather than the three instances.

Both arms of the checker were proven to fail on the defects they exist to catch
before being trusted, and one workaround inside it had to be removed for the opposite
reason: collapsing the sidebar to satisfy Playwright's click checks widened the main
column enough that the clipped labels stopped being clipped, turning a genuinely
failing run green.

**5.2's contrast half is measured and now passes.** `scripts/verify_a11y.py`
resolves each text node's foreground against its composited ancestor backgrounds and
grades it under WCAG 2.1. Its first run reported 94 findings on all nine pages at
both viewports; it now reports none, and `verify_layout.py` still reports 18/18 so
the legend rework did not overflow anything.

The path there is the instructive part, and it was **94 → 52 → 0**:

- **42 were the gate's fault.** Material Symbols ligature names (`warning`, `radar`,
  `verified`, …) render as glyphs; no reader sees those words. They are non-text
  under WCAG 1.4.11 at 3:1, not text at 4.5:1. Discrimination is by `font-family`,
  not a name list — a name list was tried and missed `model_training` immediately.
  Icons are graded at 3:1 rather than skipped, so a genuinely low-contrast icon is
  still caught.
- **40 were one palette.** Every badge and alert finding came from Streamlit's
  built-in semantic colours, not anything this project chose, so a theme-level
  override closed them all and none of the twenty call sites changed.
- **12 were the map legend, and could not be fixed the same way.** Darkening the
  palette there would have widened the gap between the legend chip and the marker it
  labels, trading a legibility defect for a correctness one on the single component
  where colour *is* the data. The legend now draws the exact `band.rgb` as a bordered
  swatch beside ordinary dark text.

Still unmeasured in 5.2: keyboard navigation, focus order, and screen-reader
announcement. Contrast was the largest gap and is closed; these are the rest.

**5.3 is measured, not estimated.** `scripts/benchmark_dashboard.py` times every page
cold and warm; the numbers are in the UI spec with an explicit statement of what the
measurement excludes (browser paint, WebGL, client-side Altair). Slowest cold render
199 ms, slowest warm median 48 ms.

**5.4 found two things AppTest could not.** The app was launched and driven in a real
browser via `.claude/launch.json`:

1. **The location selector and the freshness badges rendered twice** — once in the new
   header and once in the page — because 2.5 added the header while the pages kept
   their own `choose_province` and `freshness_badge` calls. Every AppTest assertion
   passed throughout, because "the widget exists" was true twice over. Fixed by adding
   `primary_location()` to `runtime`, which reads the header's choice without drawing a
   second control, and by removing the duplicated badge from Today and Forecast.
2. **A stale-module trap worth knowing about.** After that fix the browser showed
   `ImportError: cannot import name 'primary_location'` while AppTest passed. The
   running Streamlit process had `dashboard.runtime` cached from before the edit;
   Streamlit re-executes page scripts but does not reload already-imported modules.
   Restarting the server cleared it. This cuts both ways: a browser can show a failure
   that does not exist in the code, so a browser finding needs a restart before it is
   believed.

Not done in 5.4: screenshots. The Browser pane was not displaying, so
`computer{action:"screenshot"}` timed out and only the DOM text could be read. Layout,
spacing, contrast and the mobile breakpoint at 390x844 are therefore **still
unverified by eye** — including whether the Altair facet panels and the PyDeck legend
look right, though the timeline's axis labels and threshold annotation were confirmed
present in the DOM.

**5.2 stands at:** every badge and band carries text plus an icon (verified in the DOM),
each decision chart has a table equivalent, and all controls are native Streamlit
widgets so keyboard access comes for free. Contrast ratios and the mobile layout are
not verified.

## Phase 6 — Production roadmap (deliberately not implemented)

This phase is a plan, not code. Writing any of it now would mean guessing at
decisions that need data the project does not yet have — most obviously, five of
the eleven items below depend on a verification fact that does not exist, and
three depend on a user-identity model the project has never had.

**The gate that matters most.** Items 3–5 are the only route to publishing any
error figure. Until they exist the UI says *confidence* and never *accuracy*, and
`verify_streamlit.py` asserts the Trust page keeps saying so. That assertion is
the mechanism that stops this rule eroding quietly.

**Sequencing rationale.** Item 1 comes first because every honest statement about
observed coverage depends on knowing which station produced a reading and under
what licence. Item 2 depends on 1. Item 3 depends on 2, because verification
needs the observation that validated a forecast, and that means knowing which
observations are trustworthy. Items 6–8 are independent of that chain and could
be done sooner if user-facing value matters more than measurement.

**Effort shape, not estimates.** Items 1–5 are data-model work with tests;
6–8 are product work needing a decision from the user about identity and storage;
9 is integration work with a delivery guarantee to reason about; 10 is reporting;
11 is infrastructure and should not start until 1–10 are settled.



Dependency-ordered. Items 3–5 are the only route to publishing any accuracy
figure; until they exist the UI says confidence.

1. Station / provider / license dimension.
2. Observed-recent → observed-delayed → modeled fallback with per-row source
   label.
3. Forecast verification fact: each vintage joined to the observation that
   later validated it.
4. MAE / RMSE / bias by location, pollutant and lead hour.
5. Empirical confidence replacing the lead-time heuristic.
6. Contiguous 2h / 3h outdoor windows (closes UI gap G7).
7. Activity profile and sensitive-group preference.
8. Saved locations.
9. Alert delivery with history and status (closes G8).
10. Source and data-quality marts.
11. Serving database and deployment: only after 1–10, and only with security,
    backup and observability addressed. Nothing in this repository is
    production-ready until that work is done and verified.

### Blockers a future session should not paper over

These are the specific things that must be settled before the corresponding item
can be built honestly, rather than guessed at:

| Item | What has to be decided or obtained first |
|---|---|
| 1 | OpenAQ licence terms per provider, and whether station metadata may be redistributed |
| 3 | How long to wait before an observation counts as validating a forecast hour, and what to do when no observation ever arrives |
| 4 | Minimum paired sample size before an error figure may be shown at all — publishing MAE from a week of data would be its own false claim |
| 5 | Whether empirical confidence replaces or sits beside the current lead-time heuristic during the transition |
| 7, 8 | Where user state lives. The project has no identity model and no user store; `st.session_state` does not survive a restart |
| 9 | Delivery semantics: at-least-once versus at-most-once, and what a user sees when a send fails. The existing idempotency key is designed for the former |
| 11 | Whether DuckDB stays. The single-writer pool is an accepted local limitation; a serving database is the point at which it stops being acceptable. Also a vintage retention policy: measured growth is 1 MB per forecast run, ~1.4 GB/year at the six-hourly schedule, in the same file the dashboard reads (section J of the audit register) |

### Known limitations carried forward, unresolved

Recorded here so they are not rediscovered as surprises:

- **No screenshots exist.** The Browser pane does not composite frames in this
  environment, so screenshot calls time out and nothing has been seen as an image.
  Geometry is now measured instead, which covers the two defect classes that
  actually shipped; **contrast ratios and typography remain unverified**, and those
  are the parts an image would have helped with.
- **State and viewport coverage remain partial.** `verify_layout.py` covers
  390x844 and 1280x800 with default filters, so tablet widths remain unmeasured. The
  stale/exhausted state is now measured by the second warehouse branch in
  `verify_streamlit.py`; empty and error states are still not measured.
- **Performance is measured server-side only** (see the UI spec). Browser paint,
  WebGL setup and client-side Altair rendering are unmeasured, and the map is the
  likeliest place for a gap between the two.
- **Coverage scope**: `pyproject.toml` limits `--cov` to `vn_air_quality_weather`,
  so the `dashboard` package contributes tests but not coverage percentage. The 14
  tests added for `build_coverage` and the chart specs therefore raise the test count
  and not the percentage.

## Explicitly out of scope

Per instruction: no Kafka, no Spark, no Kubernetes. No FastAPI or PostgreSQL
added for architectural complexity alone — the current DuckDB + dbt + Streamlit
stack must be correct and usable first.
