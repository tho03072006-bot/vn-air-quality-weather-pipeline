# UI design spec — Vietnam Outdoor Decision Cockpit

Target Streamlit version: **1.60.0** (verified via
`python -c "import streamlit; print(streamlit.__version__)"`). Do not use APIs
introduced after this version.

## What already conforms

Audited before proposing changes, so the redesign does not undo working code:

| Rule | Status |
|---|---|
| `st.navigation` + `st.Page` multipage | Conforms — `dashboard/app.py:26-79` |
| Page scripts are direct scripts, not one wrapper function | Conforms |
| No `use_container_width` anywhere | Conforms — grep returns nothing |
| No `unsafe_allow_html` anywhere | Conforms |
| Theme via `.streamlit/config.toml`, no custom CSS | Conforms |
| Cache with explicit `ttl` and `max_entries` | Conforms — `runtime.py:75-146` |
| No live API call on initial render | Conforms — geocoding and on-demand
  fetch are behind explicit submit in `custom_location.py` |
| Provenance shown as text and icon, not colour alone | Conforms —
  `runtime.py:184-199` |
| Vietnamese-first copy | Conforms |

The dashboard is already a multipage decision app, not a generic admin panel.
The redesign is therefore **targeted**, not a rewrite.

## Gaps to close

### G1 — No shared component layer (Phase 2)

`dashboard/runtime.py` currently mixes three responsibilities: cached data
access (lines 75-146), formatting (173-181), and UI rendering (184-208). Pages
import rendering helpers from a module named `runtime`, which hides the seam.

Target structure:

```
dashboard/
├── components/
│   ├── app_header.py        product name, global location, freshness badge
│   ├── location_selector.py single source of truth for the chosen location
│   ├── metric_cards.py      null-safe KPI row, max 4 columns
│   ├── decision_hero.py     answer-first recommendation block
│   ├── provenance.py        source / coverage / confidence badges
│   ├── freshness.py         as-of, age, staleness status
│   ├── charts.py            Altair specs with one shared pollutant palette
│   ├── map_view.py          PyDeck layer construction + accessible table
│   └── empty_states.py      every empty state names the next action
└── view_models.py           DataFrame -> typed view objects, no Streamlit import
```

`view_models.py` must not import Streamlit, so it is unit-testable without
`AppTest`.

### G2 — KPI formatting is not null-safe (P1 bug, Phase 2)

`dashboard/app_pages/today.py:47-50` formats four metrics with `:.1f` / `:.0f`
directly off the row:

```python
metrics[1].metric("PM2.5 mô hình", f"{row['pm25_ugm3']:.1f} µg/m³")
metrics[2].metric("Cảm giác nhiệt", f"{row['apparent_temperature_c']:.1f} °C")
```

The serving mart left-joins weather (`mart_location_hourly_forecast.sql:74-76`)
and its own `confidence_level` logic explicitly tests for
`pm25_ugm3 is null or temperature_2m_c is null` (line 100), so the mart
**expects** these to be null. When they are, a pandas `NaN` renders as the
string `"nan µg/m³"`, and an object-dtype `None` raises `TypeError`.

Fix: a `metric_cards` component that renders an explicit em-dash and a
"không có dữ liệu" caption for missing values, and never format-specifies a
possibly-null value inline.

### G3 — No global location control or freshness surface (Phase 2)

Each page calls `choose_province(path, "<page>_province")` with its own widget
key (`runtime.py:156-170`). The cross-page value is preserved through
`st.session_state.selected_province`, which works, but there is no persistent
header showing where the user is, when the data is from, or how stale it is.
The sidebar carries only two static captions (`app.py:81-83`).

Target `app_header`:

- Product name.
- Global location search / selector, one widget, reused by every page.
- `data_as_of` + age + freshness status, sourced from finding E's fix.
- Observed / modeled badge.
- Secondary filters in `st.popover`.
- An explicit refresh control that clears the relevant cache entries only.

Freshness states come from `mart_current_conditions.freshness_status`, which
grades the age of the forecast vintage against the six-hourly ingest cadence
rather than against round numbers: **FRESH** to 7 hours (one cycle plus slack),
**DELAYED** to 13 hours, **STALE** beyond. The 3-hour threshold originally
sketched here was wrong — right after a 6-hourly run the age is near zero and
just before the next it is nearly six hours, so a 3-hour cut would have flagged
half of every healthy cycle as delayed.

`dashboard/runtime.py` renders these via `freshness_badge`, which pairs each
state with an icon and a plain-language age ("lấy cách đây 57 phút") so the
signal never depends on colour alone.

### G4 — Methodology repeated as `st.info` (Phase 2)

`runtime.py:202-208` renders a four-line disclaimer via `st.info`, called at the
bottom of multiple pages. Two more `st.info` methodology blocks live at
`alerts.py:79` and `custom_location.py:109`.

Repeated banner text trains users to ignore it. Move methodology into a single
`st.expander("Phương pháp và giới hạn")` component. Keep `st.info` for
transient, actionable states only (the other six uses are legitimate empty
states).

The substance of the disclaimer must survive the move: anchors are model grid
points not stations, `outdoor_score` is not VN_AQI, and this is not medical
advice.

### G5 — `st.map` cannot support the map requirements (Phase 3)

`dashboard/app_pages/national_map.py:27`:

```python
st.map(points, latitude="latitude", longitude="longitude", size="map_size", zoom=4)
```

`st.map` offers no tooltip, no selection, no legend and no layer switching, so
none of the target map behaviour is reachable.

Target: `st.pydeck_chart` with

- `ScatterplotLayer`, stable layer `id`, `pickable=True`, `auto_highlight=True`
- `tooltip` showing province, metric value, source and coverage tier
- `on_select` to open a detail panel for the clicked anchor
- `map_style=None` so the basemap follows the configured theme
- `width="stretch"`
- an always-visible legend keyed to the selected metric
- metric switch via `st.segmented_control`: PM2.5 / outdoor score / temperature
  / rain probability
- a ranking table beside the map, and a full accessible data table below it

Do **not** render polygon province coverage. One anchor per province does not
justify an area fill; a filled polygon would assert uniform coverage the data
cannot support (limitation 1 in the risk register).

### G6 — Forecast page readability (Phase 3)

Requirements for the redesign:

- Horizon via `st.segmented_control` at 24 / 48 / 72 hours.
- Small multiples per pollutant instead of one overloaded axis. Pollutants with
  different units or scales must never share a single y-axis.
- One shared pollutant colour dictionary, reused by every chart in the app.
- Weather, precipitation and pollutants in separate panels.
- Threshold rules and annotations on each panel.
- Vintage disclosure: air issued-at, weather issued-at, and lead hour, shown
  separately per finding A's fix — never one `max()`-derived timestamp.
- The word "confidence", never "accuracy", until a verification fact exists.

### G7 — Best windows are individual hours (Phase 3, depends on data fix)

`today.py:62-95` lists ranked individual hours from
`mart_outdoor_decision_window`. A user reads "khung giờ" as a contiguous block.
Until the mart produces contiguous 2h/3h windows, the UI must say it is ranking
single hours. Renaming the section is the honest interim fix; the real fix is a
contiguous-window model.

### G8 — Alerts must state preview-only (Phase 4)

`src/vn_air_quality_weather/alerts.py` evaluates rules but there is no
delivery and no persistence. The page must label itself preview-only until that
changes. Do not build a rule builder that implies alerts will be sent.

## Accessibility requirements

- Never encode meaning in colour alone. Every AQI band, coverage tier and
  decision label carries text and an icon. `runtime.py:184-199` already does
  this; extend the pattern.
- Every chart that carries a decision has a data-table equivalent.
- Keyboard reachable: all selectors are native Streamlit widgets, which gives
  this for free as long as no custom HTML is introduced.
- Check contrast against the configured theme (`primaryColor #0F766E` on
  `backgroundColor #F8FAFC`).
- Verify layout at 1440x900 and at 390x844.

## Performance

Measured with `scripts/benchmark_dashboard.py` against the demo fixture
(`data/warehouse/verify.duckdb`), median of three warm runs per page.

**Scope, stated because a benchmark with an unclear scope gets quoted as something it
never measured:** these are server-side script execution times, including the DuckDB
queries each page issues. They exclude browser paint, WebGL setup for the PyDeck map,
network transfer, and client-side Altair rendering. A page that is fast here can still
feel slow in a browser, and the map is the likeliest place for that.

| Page | Cold (ms) | Warm median (ms) |
|---|---|---|
| today | 39 | 36 |
| forecast | 99 | 48 |
| national_map | 199 | 28 |
| compare | 34 | 36 |
| history | 43 | 15 |
| custom_location | 15 | 13 |
| alerts | 17 | 12 |
| trust | 47 | 18 |
| pipeline_health | 84 | 18 |

Slowest cold render 199 ms (national_map, which reads all 34 anchors); slowest warm
median 48 ms (forecast, which melts six pollutant series into long form for the small
multiples).

| Target | Status |
|---|---|
| Main page usable, cache warm < 2 s | Met server-side (36 ms); unverified in a browser |
| Cached filter interaction < 500 ms | Met server-side (≤ 48 ms) |
| PyDeck charts per page = 1 | Met — `national_map.py` renders exactly one |
| External API calls on scheduled pages = 0 | Met — geocoding and on-demand fetch are behind explicit submit |

The earlier concern that `load_current_conditions(path, None)` reads all locations for
the map is real but not currently a problem: its cold render is the slowest of the nine
at 199 ms and its warm median is the second fastest at 28 ms, because the result is
cached for five minutes and shared with the compare and trust pages.

**Still unmeasured:** anything requiring a browser. No paint timing, no WebGL
measurement, no mobile-network simulation.

Known risks to measure: `cached_current` has `max_entries=64` and
`cached_forecast` `max_entries=128` at 5-minute TTL, which for 34 provinces is
sized reasonably, but `load_current_conditions(path, None)` reads all locations
and is called by the map page — that query must not be used where a single
province suffices.

Do not populate the table above with estimates. Measure, or leave it as
"not measured".
