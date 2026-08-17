-- One row per station, with where it is.
--
-- The coordinates arrive denormalised on every measurement row, so a station that
-- reported from two positions would produce two rows here rather than silently
-- collapsing into one. That is the honest outcome: it is visible, and
-- assert_station_position_is_singular fails on it rather than letting a decomposition
-- average two places into a location that never existed.
--
-- Nulls are expected and are not a defect. Rows ingested before the pipeline captured
-- coordinates carry none, and OpenAQ can return a location without them; a station
-- with no position simply cannot have the model sampled at it, which the
-- decomposition reports as an absent comparison rather than as a zero.
select distinct
    station_id,
    station_name,
    city_key,
    station_latitude,
    station_longitude,
    station_latitude is not null and station_longitude is not null as has_position,
    source_name,
    source_type
from {{ ref('stg_air_quality_measurements') }}
