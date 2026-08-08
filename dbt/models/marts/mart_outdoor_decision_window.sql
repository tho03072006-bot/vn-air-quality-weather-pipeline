-- A view for the same reason as mart_current_conditions: the 72-hour horizon is
-- anchored on current_timestamp, and as a table that anchor froze at build time,
-- so the ranked windows drifted into the past between dbt runs and the Today page
-- could recommend an hour that had already elapsed.
--
-- These are the highest-scoring individual hours, not contiguous blocks. Callers
-- must not present them as a continuous window until the contiguous-window model
-- exists; see the roadmap.
{{ config(materialized='view') }}

with as_of as (
    select current_timestamp as as_of_utc
),

horizon as (
    select
        forecast.*,
        as_of.as_of_utc
    from {{ ref('mart_location_hourly_forecast') }} as forecast
    cross join as_of
    where forecast.valid_at_utc >= date_trunc('hour', as_of.as_of_utc)
      and forecast.valid_at_utc < date_trunc('hour', as_of.as_of_utc) + interval '72 hours'
      and forecast.pm25_ugm3 is not null
      and forecast.temperature_2m_c is not null
)

select
    *,
    row_number() over (
        partition by location_key
        order by outdoor_score desc, valid_at_utc
    ) as suitability_rank
from horizon
qualify suitability_rank <= 5
