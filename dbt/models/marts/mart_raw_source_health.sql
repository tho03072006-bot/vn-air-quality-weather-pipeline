-- Row volume and recency of each raw table, as four rows.
--
-- Reads sources directly rather than through staging, which is the exception this
-- model exists to be: its subject *is* the raw layer, and a staging view would
-- report on the shape of a cast rather than on what landed. Every other mart reads
-- refs and should keep doing so.
--
-- Four rows out of 227,000 is the whole point. The pipeline-health page needs these
-- counts, the web application reads SQL Server, and publishing the raw tables so a
-- Java query could aggregate them would move a quarter of a million rows to produce
-- four. Aggregating here also keeps the definition in dbt, where every other number
-- the page shows is already defined.
--
-- The labels are the ones the dashboard has always shown. They are display strings,
-- not keys, and are carried here so the page and the warehouse cannot disagree about
-- what a row is called.

with air_quality_history as (
    select
        'OpenAQ/CAMS history' as source,
        count(*) as row_count,
        max(observed_at_utc) as latest_fetch_utc
    from {{ source('raw', 'air_quality_hourly') }}
),

weather_history as (
    select
        'Weather history' as source,
        count(*) as row_count,
        max(observed_at_utc) as latest_fetch_utc
    from {{ source('raw', 'weather_hourly') }}
),

air_quality_forecast as (
    select
        'Air-quality forecast' as source,
        count(*) as row_count,
        max(forecast_issued_at_utc) as latest_fetch_utc
    from {{ source('raw', 'air_quality_forecast_hourly') }}
),

weather_forecast as (
    select
        'Weather forecast' as source,
        count(*) as row_count,
        max(forecast_issued_at_utc) as latest_fetch_utc
    from {{ source('raw', 'weather_forecast_hourly') }}
)

select * from air_quality_history
union all
select * from weather_history
union all
select * from air_quality_forecast
union all
select * from weather_forecast
