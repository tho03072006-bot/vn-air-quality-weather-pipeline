select
    city_key,
    -- AT TIME ZONE 'UTC' rather than a bare cast: casting a TIMESTAMPTZ straight
    -- to DATE follows the DuckDB session TimeZone, so the same model produced a
    -- different day boundary depending on who ran it.
    cast(observed_at_utc at time zone 'UTC' as date) as data_date_utc,
    pollutant,
    unit,
    source_name,
    source_type,
    avg(concentration) as concentration_avg,
    max(concentration) as concentration_max,
    min(concentration) as concentration_min,
    count(distinct observed_at_utc) as hours_with_data,
    avg(temperature_2m_c) as temperature_avg_c,
    avg(relative_humidity_2m_pct) as humidity_avg_pct,
    sum(precipitation_mm) as precipitation_total_mm,
    avg(wind_speed_10m_kmh) as wind_speed_avg_kmh
from {{ ref('mart_city_air_quality_hourly') }}
group by 1, 2, 3, 4, 5, 6
