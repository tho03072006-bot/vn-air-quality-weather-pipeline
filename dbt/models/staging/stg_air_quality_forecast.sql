select
    cast(location_key as varchar) as location_key,
    lpad(cast(province_code as varchar), 2, '0') as province_code,
    cast(forecast_issued_at_utc as timestamptz) as forecast_issued_at_utc,
    cast(valid_at_utc as timestamptz) as valid_at_utc,
    lower(cast(pollutant as varchar)) as pollutant,
    cast(value as double) as concentration,
    'µg/m³' as unit,
    cast(grid_latitude as double) as grid_latitude,
    cast(grid_longitude as double) as grid_longitude,
    cast(source_name as varchar) as source_name,
    cast(source_type as varchar) as source_type,
    cast(resolution_note as varchar) as resolution_note
from {{ source('raw', 'air_quality_forecast_hourly') }}
where value is not null
