# Architecture and lineage

## Runtime path

```text
Airflow data interval (UTC day)
  -> Open-Meteo weather + CAMS modeled air quality
  -> OpenAQ locations + sensor-hour observations where stations exist
  -> immutable raw JSON (local in development, S3 in cloud mode)
  -> dlt merge into DuckDB raw.weather_hourly and raw.air_quality_hourly
  -> dlt merge of one audit row into raw.pipeline_runs
  -> dbt staging -> intermediate -> dimensions/facts -> analytics marts
  -> Streamlit scheduled views read analytics marts
```

The six-hour forecast path queries two Open-Meteo endpoints for the 34 province
anchors, stores every issue time as an immutable vintage, loads wide weather and
long pollutant facts, and refreshes current-condition and decision-window marts.

The on-demand path is intentionally separate:

```text
submitted Vietnam place name or WGS84 coordinate
  -> Open-Meteo Geocoding (place search only)
  -> Open-Meteo weather + CAMS air-quality forecast
  -> bounded Streamlit cache (15 minutes, maximum 128 forecast entries)
  -> temporary joined/scored dataframe
```

It creates no raw object and writes no DuckDB row. The nearest province anchor
is displayed only as a great-circle distance reference and is not an official
administrative assignment. The same transparent outdoor-score formula is used
as in `mart_location_hourly_forecast`.

## HTTP resilience

Both API clients route every request through `vn_air_quality_weather.retry`.
Transport errors and 408/425/429/500/502/503/504 responses are retried with
exponential backoff plus jitter, capped by `HTTP_BACKOFF_MAX_SECONDS`. A
`Retry-After` header always wins over the computed backoff. Non-retryable
statuses such as 401 and 404 raise on the first attempt so a bad key or a wrong
URL fails fast instead of consuming the retry budget.

OpenAQ rows use `source_type=observed`. Open-Meteo/CAMS rows use
`source_type=modeled`. They remain separate in facts, marts, filters, and charts.
Custom-location results are always `source_type=modeled` and
`coverage_tier=MODELED_ONLY`.

## Natural keys and reruns

- Weather: `city_key + observed_at_utc`.
- Air quality: `city_key + station_id + pollutant + observed_at_utc + source_name`.
- Air-quality forecast: `location_key + forecast_issued_at_utc + valid_at_utc + pollutant + source_name`.
- Weather forecast: `location_key + forecast_issued_at_utc + valid_at_utc + source_name`.
- dlt uses merge disposition with these composite primary keys.
- Raw object names contain the Airflow run ID and content hash. Identical content
  in the same run is reused; changed content creates another immutable version.

## Time contract

All extraction windows and warehouse timestamps are UTC. Business dates and
dashboard labels use `Asia/Ho_Chi_Minh` (+07:00). VN_AQI daily assigns the
01:00–00:00 local window with `vietnam_aqi_business_date`. Joins use location
plus UTC hour, never an unqualified timestamp alone.

## Model grains

- `fct_weather_hourly`: one city at one UTC hour.
- `fct_air_quality_hourly`: one city/station/pollutant/source at one UTC hour.
- `mart_city_air_quality_hourly`: one city/pollutant/source at one UTC hour.
- `mart_city_air_quality_daily`: one city/pollutant/source per UTC date.
- `mart_data_coverage`: one city/pollutant/source per UTC date.
- `int_city_pollutant_hourly`: one city/pollutant/source at one UTC hour on a
  dense spine, carrying the Nowcast weighted mean.
- `mart_city_aqi_hourly`: one city/source at one UTC hour.
- `mart_city_aqi_daily`: one city/source per Vietnam AQI business date.
- `dim_province`: the 34 units effective 1 July 2025.
- `dim_location`: one precomputed model anchor per province; extensible to cached custom locations.
- `fct_air_quality_forecast`: one location/pollutant/issue/valid hour.
- `fct_weather_forecast`: one location/issue/valid hour.
- `mart_location_hourly_forecast`: latest vintage with modeled pollutants, weather and decision score.
- `mart_current_conditions`: nearest available valid hour per province anchor.
- `mart_outdoor_decision_window`: top five explainable hours per location in the next 72 hours.
- `mart_outdoor_contiguous_window`: one contiguous 2h or 3h candidate per
  location/start/duration in the next 72 hours; its score is the worst member
  hour and missing required data breaks the candidate.
- `fct_pipeline_run`: one run_id per UTC data date.

## VN_AQI

The index follows Quyet dinh 1459/QD-TCMT dated 12 November 2019, which
replaced Quyet dinh 878/QD-TCMT. Only the four pollutants this pipeline
collects are modelled; SO2 and CO are out of scope.

- `dim_vn_aqi_breakpoint` holds Bang 2 as one row per linear segment, in
  micrograms per cubic metre. O3 carries two scales because the decision gives
  separate 1-hour and 8-hour curves, and the 8-hour curve stops at level 5.
- `dim_vn_aqi_category` holds Bang 1, 4 and 5: bands, RGB colours, health
  effects and activity advice.
- Hourly index: PM2.5 and PM10 use the Nowcast weighted mean over the trailing
  twelve hours, NO2 and O3 use the plain 1-hour mean, and `AQIh = max(AQIx)`.
- Daily index: PM uses the 24-hour mean, NO2 uses the highest 1-hour mean, O3
  takes the larger of the 1-hour and 8-hour sub-indices, and the 8-hour branch
  is dropped above 400 ug/m3.

Documented interpretation choices:
- The decision does not state a minimum completeness for the rolling 8-hour
  ozone mean; this project requires at least six of the eight hours.
- `is_publishable` encodes muc 2.1: without PM10 or PM2.5 the index must not be
  published, and the dashboard filters on it.
- The decision is written for validated continuous-monitoring stations. CAMS
  rows are modeled, so any VN_AQI computed from them is an estimate and is
  labelled as such.
- Muc 2.3 of the decision prints AQI_NO2 = 60 for a 118.7 ug/m3 1-hour mean,
  but Cong thuc 1 yields 59.35. The formula is treated as authoritative and
  that single example is excluded from the regression test.

## Pipeline audit

`raw.pipeline_runs` receives one merged row per run, keyed on
`run_id + data_date`. `fct_pipeline_run` exposes duration, row counts per
source and `is_latest_run_for_date`. dbt source freshness warns after 36 hours
and errors after 72 hours on `finished_at_utc`.
