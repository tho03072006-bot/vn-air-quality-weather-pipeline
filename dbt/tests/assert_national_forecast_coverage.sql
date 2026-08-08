select province_code
from {{ ref('dim_province') }}
except
select distinct province_code
from {{ ref('mart_location_hourly_forecast') }}
