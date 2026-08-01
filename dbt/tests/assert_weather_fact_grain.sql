select city_key, observed_at_utc, count(*) as row_count
from {{ ref('fct_weather_hourly') }}
group by 1, 2
having count(*) > 1
