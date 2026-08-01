select
    cast(city_key as varchar) as city_key,
    cast(observed_at_utc as timestamptz) as observed_at_utc,
    cast(temperature_2m as double) as temperature_2m_c,
    cast(relative_humidity_2m as double) as relative_humidity_2m_pct,
    cast(precipitation as double) as precipitation_mm,
    cast(wind_speed_10m as double) as wind_speed_10m_kmh,
    cast(wind_direction_10m as double) as wind_direction_10m_deg,
    cast(grid_latitude as double) as grid_latitude,
    cast(grid_longitude as double) as grid_longitude,
    cast(source_name as varchar) as source_name
from {{ source('raw', 'weather_hourly') }}
