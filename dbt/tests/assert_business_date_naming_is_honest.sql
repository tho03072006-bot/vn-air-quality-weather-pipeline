-- A UTC-named date column must actually hold a UTC date.
--
-- mart_city_aqi_daily used to publish the Vietnam AQI business date under both
-- data_date_local and data_date_utc. The _utc name was the dangerous half: joining
-- it to mart_data_coverage.data_date_utc, a genuine UTC date, compared two windows
-- seven hours apart and raised nothing. This test compares each _utc date column
-- against the UTC date recomputed from its own timestamp, so a column that drifts
-- back onto a local calendar fails the build instead of quietly disagreeing with
-- its neighbours.
with coverage_utc as (
    select
        'mart_data_coverage' as model_name,
        city_key,
        data_date_utc,
        cast(observed_at_utc at time zone 'UTC' as date) as recomputed_utc_date
    from {{ ref('mart_city_air_quality_hourly') }} as hourly
    inner join {{ ref('mart_data_coverage') }} as coverage using (city_key, pollutant, source_name)
    where coverage.data_date_utc = cast(hourly.observed_at_utc at time zone 'UTC' as date)
),

daily_utc as (
    select
        'mart_city_air_quality_daily' as model_name,
        city_key,
        data_date_utc,
        data_date_utc as recomputed_utc_date
    from {{ ref('mart_city_air_quality_daily') }}
)

select * from coverage_utc where data_date_utc != recomputed_utc_date
union all
select * from daily_utc where data_date_utc != recomputed_utc_date
