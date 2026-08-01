with air_quality as (
    select
        city_key,
        date_trunc('hour', observed_at_utc) as observed_at_utc,
        pollutant,
        unit,
        source_name,
        source_type,
        avg(concentration) as concentration,
        count(distinct station_id) as station_count,
        sum(case when flagged then 1 else 0 end) as flagged_measurement_count
    from {{ ref('fct_air_quality_hourly') }}
    group by 1, 2, 3, 4, 5, 6
),
weather as (
    select
        city_key,
        date_trunc('hour', observed_at_utc) as observed_at_utc,
        avg(temperature_2m_c) as temperature_2m_c,
        avg(relative_humidity_2m_pct) as relative_humidity_2m_pct,
        sum(precipitation_mm) as precipitation_mm,
        avg(wind_speed_10m_kmh) as wind_speed_10m_kmh,
        avg(wind_direction_10m_deg) as wind_direction_10m_deg
    from {{ ref('fct_weather_hourly') }}
    group by 1, 2
)
select
    air_quality.*,
    weather.temperature_2m_c,
    weather.relative_humidity_2m_pct,
    weather.precipitation_mm,
    weather.wind_speed_10m_kmh,
    weather.wind_direction_10m_deg,
    timezone('Asia/Ho_Chi_Minh', air_quality.observed_at_utc) as observed_at_local
from air_quality
left join weather using (city_key, observed_at_utc)
