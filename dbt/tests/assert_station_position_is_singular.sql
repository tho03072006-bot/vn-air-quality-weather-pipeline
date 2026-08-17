-- A station must report from one place, or the decomposition is measuring a fiction.
--
-- dim_air_quality_station is built with `select distinct` over rows that each carry
-- their own coordinates, so a station whose position changed between ingestions
-- produces two rows rather than one. That is deliberate -- collapsing them would pick
-- a winner silently -- but it must not pass unnoticed, because sampling the model "at
-- the station" then means sampling it at whichever position an aggregate happened to
-- choose, and the representativeness term would be measured against a location that
-- never existed.
--
-- Rows with no position are excluded rather than flagged. A station that predates
-- coordinate capture has one known position and one unknown, which is not a conflict.
with positioned as (
    select station_id, city_key, station_latitude, station_longitude
    from {{ ref('dim_air_quality_station') }}
    where has_position
)

select
    station_id,
    city_key,
    count(*) as distinct_positions
from positioned
group by station_id, city_key
having count(*) > 1
