-- The Nowcast spine must hold exactly one row per city, pollutant, source and
-- UTC hour. A duplicate here would silently corrupt every positional weight,
-- and the AQI marts would hide it behind their own group by.
select
    city_key,
    pollutant,
    source_name,
    source_type,
    observed_at_utc,
    count(*) as row_count
from {{ ref('int_city_pollutant_hourly') }}
group by 1, 2, 3, 4, 5
having count(*) > 1
