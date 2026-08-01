-- VN_AQI ngay (AQId) per city, UTC calendar date and data source.
--
-- Muc 2.2.2 inputs:
--   PM2.5 / PM10 -> 24-hour mean
--   NO2          -> highest 1-hour mean in the day
--   O3           -> the larger of the AQI from the highest 1-hour mean and the
--                   AQI from the highest 8-hour mean, and the 8-hour branch is
--                   dropped entirely above 400 ug/m3.
-- The decision defines the day as 01:00 to 00:00 local. Storage here is UTC by
-- contract, so the grain is the UTC calendar date; the presentation layer is
-- the only place that shifts to Asia/Ho_Chi_Minh.
with hourly as (
    select * from {{ ref('int_city_pollutant_hourly') }}
),

ozone_rolling as (
    select
        city_key,
        source_name,
        source_type,
        observed_at_utc,
        avg(concentration) over eight_hours as mean_8h,
        count(concentration) over eight_hours as hours_in_window
    from hourly
    where pollutant = 'o3'
    window eight_hours as (
        partition by city_key, source_name, source_type
        order by observed_at_utc
        rows between 7 preceding and current row
    )
),

ozone_daily as (
    select
        city_key,
        source_name,
        source_type,
        -- Casting TIMESTAMPTZ to DATE uses the session TimeZone, which is the
        -- machine zone by default. The explicit AT TIME ZONE keeps the grain on
        -- the UTC calendar on a developer laptop in Asia/Ho_Chi_Minh as well.
        cast(observed_at_utc at time zone 'UTC' as date) as data_date_utc,
        -- At least six of the eight hours must be present for the mean to count.
        max(case when hours_in_window >= 6 then mean_8h end) as o3_max_8h
    from ozone_rolling
    group by 1, 2, 3, 4
),

daily_inputs as (
    select
        city_key,
        source_name,
        source_type,
        -- Casting TIMESTAMPTZ to DATE uses the session TimeZone, which is the
        -- machine zone by default. The explicit AT TIME ZONE keeps the grain on
        -- the UTC calendar on a developer laptop in Asia/Ho_Chi_Minh as well.
        cast(observed_at_utc at time zone 'UTC' as date) as data_date_utc,
        avg(case when pollutant = 'pm25' then concentration end) as pm25_mean_24h,
        avg(case when pollutant = 'pm10' then concentration end) as pm10_mean_24h,
        max(case when pollutant = 'no2' then concentration end) as no2_max_1h,
        max(case when pollutant = 'o3' then concentration end) as o3_max_1h,
        count(case when pollutant = 'pm25' and concentration is not null then 1 end)
            as pm25_hours,
        count(case when pollutant = 'pm10' and concentration is not null then 1 end)
            as pm10_hours
    from hourly
    group by 1, 2, 3, 4
),

joined as (
    select
        daily_inputs.*,
        ozone_daily.o3_max_8h
    from daily_inputs
    left join ozone_daily
        on daily_inputs.city_key = ozone_daily.city_key
        and daily_inputs.source_name = ozone_daily.source_name
        and daily_inputs.source_type = ozone_daily.source_type
        and daily_inputs.data_date_utc = ozone_daily.data_date_utc
),

components as (
    select city_key, source_name, source_type, data_date_utc,
        'pm25' as pollutant, 'pm25' as aqi_scale_key, pm25_mean_24h as aqi_input
    from joined
    union all
    select city_key, source_name, source_type, data_date_utc,
        'pm10', 'pm10', pm10_mean_24h
    from joined
    union all
    select city_key, source_name, source_type, data_date_utc,
        'no2', 'no2', no2_max_1h
    from joined
    union all
    select city_key, source_name, source_type, data_date_utc,
        'o3', 'o3_1h', o3_max_1h
    from joined
    union all
    select city_key, source_name, source_type, data_date_utc,
        'o3', 'o3_8h', case when o3_max_8h > 400 then null else o3_max_8h end
    from joined
),

scored as (
    select
        components.city_key,
        components.source_name,
        components.source_type,
        components.data_date_utc,
        components.pollutant,
        {{ vn_aqi_value('components.aqi_input') }} as aqi_component
    from components
    left join {{ ref('dim_vn_aqi_breakpoint') }} as bp
        on {{ vn_aqi_join_condition('components.aqi_input', 'components.aqi_scale_key') }}
),

per_pollutant as (
    select
        city_key,
        source_name,
        source_type,
        data_date_utc,
        max(case when pollutant = 'pm25' then aqi_component end) as aqi_pm25,
        max(case when pollutant = 'pm10' then aqi_component end) as aqi_pm10,
        max(case when pollutant = 'no2' then aqi_component end) as aqi_no2,
        max(case when pollutant = 'o3' then aqi_component end) as aqi_o3,
        max(aqi_component) as aqi_daily
    from scored
    group by 1, 2, 3, 4
),

labelled as (
    select
        per_pollutant.*,
        joined.pm25_mean_24h,
        joined.pm10_mean_24h,
        joined.no2_max_1h,
        joined.o3_max_1h,
        joined.o3_max_8h,
        joined.pm25_hours,
        joined.pm10_hours,
        greatest(joined.pm25_hours, joined.pm10_hours) >= 18 as has_particulate_input,
        case
            when per_pollutant.aqi_daily is null then null
            when per_pollutant.aqi_daily = per_pollutant.aqi_pm25 then 'pm25'
            when per_pollutant.aqi_daily = per_pollutant.aqi_pm10 then 'pm10'
            when per_pollutant.aqi_daily = per_pollutant.aqi_no2 then 'no2'
            when per_pollutant.aqi_daily = per_pollutant.aqi_o3 then 'o3'
        end as dominant_pollutant
    from per_pollutant
    inner join joined
        on per_pollutant.city_key = joined.city_key
        and per_pollutant.source_name = joined.source_name
        and per_pollutant.source_type = joined.source_type
        and per_pollutant.data_date_utc = joined.data_date_utc
)

select
    labelled.city_key,
    labelled.source_name,
    labelled.source_type,
    labelled.data_date_utc,
    labelled.aqi_daily,
    labelled.dominant_pollutant,
    labelled.aqi_pm25,
    labelled.aqi_pm10,
    labelled.aqi_no2,
    labelled.aqi_o3,
    labelled.pm25_mean_24h,
    labelled.pm10_mean_24h,
    labelled.no2_max_1h,
    labelled.o3_max_1h,
    labelled.o3_max_8h,
    labelled.pm25_hours,
    labelled.pm10_hours,
    labelled.has_particulate_input,
    labelled.has_particulate_input as is_publishable,
    category.category_vi,
    category.category_en,
    category.colour_hex,
    category.health_effect_vi,
    category.advice_general_vi,
    category.advice_sensitive_vi
from labelled
left join {{ ref('dim_vn_aqi_category') }} as category
    on labelled.aqi_daily between category.aqi_low and category.aqi_high
