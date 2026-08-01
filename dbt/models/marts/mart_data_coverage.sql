select
    city_key,
    cast(observed_at_utc as date) as data_date_utc,
    pollutant,
    source_name,
    source_type,
    count(distinct observed_at_utc) as hours_with_data,
    24 as expected_hours,
    least(count(distinct observed_at_utc) / 24.0, 1.0) as coverage_ratio,
    24 - least(count(distinct observed_at_utc), 24) as missing_hours
from {{ ref('mart_city_air_quality_hourly') }}
group by 1, 2, 3, 4, 5
