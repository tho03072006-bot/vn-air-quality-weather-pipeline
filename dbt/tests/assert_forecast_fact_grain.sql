select
    location_key,
    forecast_issued_at_utc,
    valid_at_utc,
    pollutant,
    source_name,
    count(*) as row_count
from {{ ref('fct_air_quality_forecast') }}
group by 1, 2, 3, 4, 5
having count(*) > 1
