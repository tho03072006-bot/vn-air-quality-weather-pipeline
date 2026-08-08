select
    cast(location_key as varchar) as location_key,
    lpad(cast(province_code as varchar), 2, '0') as province_code,
    cast(forecast_issued_at_utc as timestamptz) as forecast_issued_at_utc,
    cast(valid_at_utc as timestamptz) as valid_at_utc,
    cast(temperature_2m as double) as temperature_2m_c,
    cast(apparent_temperature as double) as apparent_temperature_c,
    cast(relative_humidity_2m as double) as relative_humidity_2m_pct,
    cast(precipitation_probability as double) as precipitation_probability_pct,
    cast(precipitation as double) as precipitation_mm,
    cast(wind_speed_10m as double) as wind_speed_10m_kmh,
    cast(wind_direction_10m as double) as wind_direction_10m_deg,
    cast(uv_index as double) as uv_index,
    cast(grid_latitude as double) as grid_latitude,
    cast(grid_longitude as double) as grid_longitude,
    cast(source_name as varchar) as source_name
from {{ source('raw', 'weather_forecast_hourly') }}
