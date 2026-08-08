-- Provider-flagged measurements are excluded from the published concentration
-- rather than averaged into it. A flag means the provider itself does not stand
-- behind the reading, so letting it into an official-looking mart -- and from
-- there into the VN_AQI models and the dashboard -- publishes a number its own
-- source doubts. The previous version computed flagged_measurement_count and
-- then reported it without acting on it, so the count documented an exclusion
-- that never happened.
--
-- Excluding is a decision, not a deletion: the withheld count is published beside
-- the value, and the rows themselves stay queryable in fct_air_quality_hourly and
-- in mart_flagged_measurement_quarantine.
--
-- An hour whose only readings are flagged has no publishable value, so it is
-- dropped rather than surfaced as a NULL concentration that coverage would still
-- count as data. Downstream that becomes a gap, which int_city_pollutant_hourly
-- already models correctly for the Nowcast window.
with air_quality as (
    select
        city_key,
        date_trunc('hour', observed_at_utc) as observed_at_utc,
        pollutant,
        unit,
        source_name,
        source_type,
        avg(case when not flagged then concentration end) as concentration,
        count(distinct case when not flagged then station_id end) as station_count,
        count(case when not flagged then 1 end) as included_measurement_count,
        count(case when flagged then 1 end) as excluded_flagged_count
    from {{ ref('fct_air_quality_hourly') }}
    group by 1, 2, 3, 4, 5, 6
    having count(case when not flagged then 1 end) > 0
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
