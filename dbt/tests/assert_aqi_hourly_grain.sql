-- The hourly AQI mart must stay at one row per city, source and UTC hour.
select
    city_key,
    source_name,
    source_type,
    observed_at_utc,
    count(*) as row_count
from {{ ref('mart_city_aqi_hourly') }}
group by 1, 2, 3, 4
having count(*) > 1
