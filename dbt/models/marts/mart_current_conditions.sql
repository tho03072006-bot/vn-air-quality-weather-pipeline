-- Materialised as a view on purpose, overriding the table default for marts.
-- current_timestamp inside a table model is evaluated once at build time, so
-- "current conditions" froze at the last dbt run and then aged in silence: six
-- hours later it still pointed at the hour that was current when it was built,
-- with nothing in the data admitting it. As a view the clock is read per query,
-- and as_of_utc records which clock reading produced the row.
{{ config(materialized='view') }}

with as_of as (
    select current_timestamp as as_of_utc
),

candidates as (
    select
        forecast.*,
        as_of.as_of_utc
    from {{ ref('mart_location_hourly_forecast') }} as forecast
    cross join as_of
    where forecast.valid_at_utc >= date_trunc('hour', as_of.as_of_utc) - interval '1 hour'
),

nearest as (
    select *
    from candidates
    qualify row_number() over (
        partition by location_key
        order by abs(epoch(valid_at_utc - as_of_utc)), forecast_issued_at_utc desc
    ) = 1
)

select
    *,
    -- Signed. A negative value means the nearest forecast hour is still ahead of
    -- now, which is normal: the selection takes the closest hour, not the last
    -- elapsed one.
    cast(round(epoch(as_of_utc - valid_at_utc) / 60.0) as integer) as data_age_minutes,
    cast(round(epoch(as_of_utc - forecast_issued_at_utc) / 60.0) as integer)
        as forecast_age_minutes,
    -- Thresholds follow the six-hourly forecast cadence rather than round
    -- numbers. One cycle plus slack is still healthy; two missed cycles is not.
    -- Picking 3h here would flag half of every normal cycle as delayed.
    case
        when epoch(as_of_utc - forecast_issued_at_utc) / 3600.0 <= 7 then 'FRESH'
        when epoch(as_of_utc - forecast_issued_at_utc) / 3600.0 <= 13 then 'DELAYED'
        else 'STALE'
    end as freshness_status
from nearest
