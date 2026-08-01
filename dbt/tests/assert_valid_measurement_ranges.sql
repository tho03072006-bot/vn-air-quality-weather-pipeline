select *
from {{ ref('mart_city_air_quality_hourly') }}
where concentration < 0
   or relative_humidity_2m_pct not between 0 and 100
   or temperature_2m_c not between -30 and 60
   or wind_speed_10m_kmh < 0
   or precipitation_mm < 0
