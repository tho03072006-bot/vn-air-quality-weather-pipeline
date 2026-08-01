-- VN_AQI gio (AQIh) per city, UTC hour and data source.
--
-- Muc 2.2.1: PM2.5 and PM10 use the Nowcast weighted mean, NO2 and O3 use the
-- plain 1-hour mean, and the published index is the maximum across pollutants.
-- Observed and modeled sources are kept apart so a station reading is never
-- averaged with a CAMS grid estimate.
with basis as (
    select
        city_key,
        source_name,
        source_type,
        observed_at_utc,
        pollutant,
        case
            when pollutant in ('pm25', 'pm10') then nowcast_concentration
            else concentration
        end as aqi_input,
        case
            when pollutant = 'o3' then 'o3_1h'
            else pollutant
        end as aqi_scale_key
    from {{ ref('int_city_pollutant_hourly') }}
),

scored as (
    select
        basis.city_key,
        basis.source_name,
        basis.source_type,
        basis.observed_at_utc,
        basis.pollutant,
        basis.aqi_input,
        {{ vn_aqi_value('basis.aqi_input') }} as aqi_pollutant
    from basis
    left join {{ ref('dim_vn_aqi_breakpoint') }} as bp
        on {{ vn_aqi_join_condition('basis.aqi_input', 'basis.aqi_scale_key') }}
),

pivoted as (
    select
        city_key,
        source_name,
        source_type,
        observed_at_utc,
        max(case when pollutant = 'pm25' then aqi_pollutant end) as aqi_pm25,
        max(case when pollutant = 'pm10' then aqi_pollutant end) as aqi_pm10,
        max(case when pollutant = 'no2' then aqi_pollutant end) as aqi_no2,
        max(case when pollutant = 'o3' then aqi_pollutant end) as aqi_o3,
        max(case when pollutant = 'pm25' then aqi_input end) as nowcast_pm25,
        max(case when pollutant = 'pm10' then aqi_input end) as nowcast_pm10,
        max(aqi_pollutant) as aqi_hourly,
        count(aqi_pollutant) as scored_pollutant_count,
        -- Muc 2.1: the index may only be published when PM10 or PM2.5 is present.
        max(
            case when pollutant in ('pm25', 'pm10') and aqi_pollutant is not null then 1 else 0 end
        ) = 1 as has_particulate_input
    from scored
    group by 1, 2, 3, 4
),

labelled as (
    select
        pivoted.*,
        case
            when pivoted.aqi_hourly is null then null
            when pivoted.aqi_hourly = pivoted.aqi_pm25 then 'pm25'
            when pivoted.aqi_hourly = pivoted.aqi_pm10 then 'pm10'
            when pivoted.aqi_hourly = pivoted.aqi_no2 then 'no2'
            when pivoted.aqi_hourly = pivoted.aqi_o3 then 'o3'
        end as dominant_pollutant
    from pivoted
)

select
    labelled.city_key,
    labelled.source_name,
    labelled.source_type,
    labelled.observed_at_utc,
    timezone('Asia/Ho_Chi_Minh', labelled.observed_at_utc) as observed_at_local,
    labelled.aqi_hourly,
    labelled.dominant_pollutant,
    labelled.aqi_pm25,
    labelled.aqi_pm10,
    labelled.aqi_no2,
    labelled.aqi_o3,
    labelled.nowcast_pm25,
    labelled.nowcast_pm10,
    labelled.scored_pollutant_count,
    labelled.has_particulate_input,
    labelled.has_particulate_input as is_publishable,
    category.category_vi,
    category.category_en,
    category.colour_hex,
    category.advice_general_vi,
    category.advice_sensitive_vi
from labelled
left join {{ ref('dim_vn_aqi_category') }} as category
    on labelled.aqi_hourly between category.aqi_low and category.aqi_high
