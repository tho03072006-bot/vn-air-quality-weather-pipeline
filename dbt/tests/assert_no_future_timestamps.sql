select *
from {{ ref('mart_city_air_quality_hourly') }}
where observed_at_utc > current_timestamp + interval '1 hour'
