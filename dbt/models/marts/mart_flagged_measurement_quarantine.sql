-- Every provider-flagged measurement that mart_city_air_quality_hourly withheld.
--
-- Quarantine rather than deletion. An exclusion policy that cannot be reviewed is
-- indistinguishable from data loss: an analyst who sees a gap needs to be able to
-- ask whether the hour was never collected or was collected and rejected, and
-- which station did the rejecting.
select
    city_key,
    station_id,
    station_name,
    sensor_id,
    pollutant,
    unit,
    observed_at_utc,
    {{ vietnam_aqi_business_date('observed_at_utc') }} as data_date_vn,
    timezone('Asia/Ho_Chi_Minh', observed_at_utc) as observed_at_local,
    concentration,
    source_name,
    source_type
from {{ ref('fct_air_quality_hourly') }}
where flagged
