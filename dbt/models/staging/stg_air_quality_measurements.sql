select
    cast(city_key as varchar) as city_key,
    cast(station_id as varchar) as station_id,
    cast(station_name as varchar) as station_name,
    cast(sensor_id as bigint) as sensor_id,
    lower(cast(pollutant as varchar)) as pollutant,
    cast(unit as varchar) as unit,
    cast(observed_at_utc as timestamptz) as observed_at_utc,
    cast(value as double) as concentration,
    coalesce(cast(flagged as boolean), false) as flagged,
    cast(source_name as varchar) as source_name,
    cast(source_type as varchar) as source_type
from {{ source('raw', 'air_quality_hourly') }}
