# Architecture and lineage

## Runtime path

```text
Airflow data interval (UTC day)
  -> Open-Meteo weather + CAMS modeled air quality
  -> OpenAQ locations + sensor-hour observations where stations exist
  -> immutable raw JSON (local in development, S3 in cloud mode)
  -> dlt merge into DuckDB raw.weather_hourly and raw.air_quality_hourly
  -> dbt staging -> dimensions/facts -> analytics marts
  -> Streamlit reads analytics marts only
```

OpenAQ rows use `source_type=observed`. Open-Meteo/CAMS rows use
`source_type=modeled`. They remain separate in facts, marts, filters, and charts.

## Natural keys and reruns

- Weather: `city_key + observed_at_utc`.
- Air quality: `city_key + station_id + pollutant + observed_at_utc + source_name`.
- dlt uses merge disposition with these composite primary keys.
- Raw object names contain the Airflow run ID and content hash. Identical content
  in the same run is reused; changed content creates another immutable version.

## Time contract

All extraction windows and warehouse timestamps are UTC. Dashboard local-time
labels use `Asia/Ho_Chi_Minh` (+07:00). Joins use city plus UTC hour, never an
unqualified timestamp alone.

## Model grains

- `fct_weather_hourly`: one city at one UTC hour.
- `fct_air_quality_hourly`: one city/station/pollutant/source at one UTC hour.
- `mart_city_air_quality_hourly`: one city/pollutant/source at one UTC hour.
- `mart_city_air_quality_daily`: one city/pollutant/source per UTC date.
- `mart_data_coverage`: one city/pollutant/source per UTC date.
