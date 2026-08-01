select
    city_key, station_id, pollutant, source_name, observed_at_utc, count(*) as row_count
from {{ ref('fct_air_quality_hourly') }}
group by 1, 2, 3, 4, 5
having count(*) > 1
