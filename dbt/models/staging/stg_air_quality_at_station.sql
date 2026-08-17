-- The model sampled where the instrument stands, normalised to the measurement grain.
--
-- Guarded against the source not existing at all, which is not a hypothetical: this
-- table was added after warehouses already existed, and the published release asset
-- is rebuilt every six hours from its own previous copy. A model that assumed the
-- table would fail the refresh workflow with "table does not exist" -- the same class
-- of break that adding station_latitude to stg_air_quality_measurements caused, one
-- step further along.
--
-- The empty branch is typed rather than `select null`: downstream joins and casts
-- have to keep compiling, so the columns must exist with the right types even when
-- there are no rows to put in them. A warehouse with no station sampling then
-- produces a decomposition with no representativeness rows, which is the honest
-- answer -- the comparison was never made, rather than made and found to be zero.
{% set raw_relation = adapter.get_relation(
    database=target.database, schema='raw', identifier='air_quality_at_station_hourly'
) %}

{% if raw_relation is none %}

select
    cast(null as varchar) as station_id,
    cast(null as varchar) as city_key,
    cast(null as varchar) as pollutant,
    cast(null as varchar) as unit,
    cast(null as timestamptz) as observed_at_utc,
    cast(null as double) as concentration,
    cast(null as double) as station_latitude,
    cast(null as double) as station_longitude,
    cast(null as varchar) as source_name,
    cast(null as varchar) as source_type
where false

{% else %}

select
    cast(station_id as varchar) as station_id,
    cast(city_key as varchar) as city_key,
    lower(cast(pollutant as varchar)) as pollutant,
    trim(lower(cast(unit as varchar))) as unit,
    cast(observed_at_utc as timestamptz) as observed_at_utc,
    cast(value as double) as concentration,
    cast(station_latitude as double) as station_latitude,
    cast(station_longitude as double) as station_longitude,
    cast(source_name as varchar) as source_name,
    cast(source_type as varchar) as source_type
from {{ raw_relation }}

{% endif %}
