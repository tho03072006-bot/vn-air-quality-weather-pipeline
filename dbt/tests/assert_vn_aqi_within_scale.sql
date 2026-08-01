-- Every published VN_AQI value must sit on the 0-500 scale and, when present,
-- must carry a category label from Bang 1.
select
    'mart_city_aqi_hourly' as model_name,
    city_key,
    source_type,
    cast(observed_at_utc as varchar) as grain_value,
    aqi_hourly as aqi_value,
    category_vi
from {{ ref('mart_city_aqi_hourly') }}
where aqi_hourly is not null
  and (aqi_hourly < 0 or aqi_hourly > 500 or category_vi is null)

union all

select
    'mart_city_aqi_daily',
    city_key,
    source_type,
    cast(data_date_utc as varchar),
    aqi_daily,
    category_vi
from {{ ref('mart_city_aqi_daily') }}
where aqi_daily is not null
  and (aqi_daily < 0 or aqi_daily > 500 or category_vi is null)
