# Serving data dictionary

## Geography

- `analytics.dim_province`: 34 province-level units effective 1 July 2025,
  official two-digit code, Vietnamese name, unit type, representative WGS84
  anchor and `Asia/Ho_Chi_Minh` timezone.
- `analytics.dim_location`: current precomputed province anchors. On-demand
  custom coordinates are deliberately not inserted into this warehouse dimension.

## Forecast facts

- `analytics.fct_air_quality_forecast`: immutable issue/valid-time vintages at
  location and pollutant grain. In this MVP, `forecast_issued_at_utc` is the
  pipeline fetch/vintage timestamp because the response does not expose a
  provider model-run timestamp. Values are modeled µg/m³, never observations.
- `analytics.fct_weather_forecast`: immutable issue/valid-time weather vintages.

## Decision marts

- `analytics.mart_location_hourly_forecast`: latest available forecast vintage,
  local valid time, lead time, pollutants, weather, source/coverage/confidence,
  and an explainable 0–100 outdoor planning score.
- `analytics.mart_current_conditions`: nearest available forecast valid hour for
  each province anchor.
- `analytics.mart_outdoor_decision_window`: five strongest complete hours in the
  next 72 hours for each anchor.

`outdoor_score` is not VN_AQI and is not medical advice. `MODELED_ONLY` means no
suitable observation was used for that row.

## Ephemeral on-demand view

The custom-location page builds an in-memory dataframe with the same pollutant,
weather, provenance, confidence and outdoor-score columns as
`mart_location_hourly_forecast`. It is cached for at most 15 minutes and has no
warehouse relation, persistence contract or official administrative code.
