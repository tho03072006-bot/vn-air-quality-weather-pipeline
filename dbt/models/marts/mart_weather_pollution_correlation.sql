select
    city_key,
    source_name,
    source_type,
    count(*) as paired_hours,
    corr(concentration, temperature_2m_c) as pm25_temperature_correlation,
    corr(concentration, relative_humidity_2m_pct) as pm25_humidity_correlation,
    corr(concentration, wind_speed_10m_kmh) as pm25_wind_speed_correlation,
    corr(concentration, precipitation_mm) as pm25_precipitation_correlation
from {{ ref('mart_city_air_quality_hourly') }}
where pollutant = 'pm25'
  and concentration is not null
group by 1, 2, 3
