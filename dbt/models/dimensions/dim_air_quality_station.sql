select distinct
    station_id,
    station_name,
    city_key,
    source_name,
    source_type
from {{ ref('stg_air_quality_measurements') }}
