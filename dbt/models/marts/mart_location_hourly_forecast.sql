-- One forecast vintage per province anchor, served whole.
-- outdoor_score is a transparent planning heuristic, not a health or VN_AQI index.
--
-- The vintage is resolved once per anchor rather than per pollutant. Resolving it
-- per pollutant lets a partially refreshed batch serve PM2.5 from the 12:00 run
-- and O3 from the 06:00 run while reporting a single issued-at, which is a
-- forecast that no model run ever produced. Air and weather vintages are both
-- published instead of collapsed with max(), because when they disagree the
-- consumer needs to see that rather than be shown the newer of the two.
with air_vintage as (
    select
        location_key,
        max(forecast_issued_at_utc) as forecast_issued_at_utc
    from {{ ref('fct_air_quality_forecast') }}
    group by 1
),

air_current as (
    select fact.*
    from {{ ref('fct_air_quality_forecast') }} as fact
    inner join air_vintage as vintage
        on fact.location_key = vintage.location_key
        and fact.forecast_issued_at_utc = vintage.forecast_issued_at_utc
),

air_pivot as (
    -- forecast_issued_at_utc is constant inside one vintage, so grouping by it
    -- is exact rather than an aggregate that hides disagreement.
    select
        location_key,
        province_code,
        forecast_issued_at_utc,
        valid_at_utc,
        max(case when pollutant = 'pm25' then concentration end) as pm25_ugm3,
        max(case when pollutant = 'pm10' then concentration end) as pm10_ugm3,
        max(case when pollutant = 'no2' then concentration end) as no2_ugm3,
        max(case when pollutant = 'o3' then concentration end) as o3_ugm3,
        max(case when pollutant = 'so2' then concentration end) as so2_ugm3,
        max(case when pollutant = 'co' then concentration end) as co_ugm3,
        max(source_name) as air_quality_source,
        max(resolution_note) as resolution_note
    from air_current
    group by 1, 2, 3, 4
),

weather_vintage as (
    select
        location_key,
        max(forecast_issued_at_utc) as forecast_issued_at_utc
    from {{ ref('fct_weather_forecast') }}
    group by 1
),

weather_current as (
    select fact.*
    from {{ ref('fct_weather_forecast') }} as fact
    inner join weather_vintage as vintage
        on fact.location_key = vintage.location_key
        and fact.forecast_issued_at_utc = vintage.forecast_issued_at_utc
),

joined as (
    select
        air.location_key,
        air.province_code,
        province.province_name,
        province.unit_type,
        province.anchor_name,
        province.latitude,
        province.longitude,
        air.forecast_issued_at_utc,
        weather.forecast_issued_at_utc as weather_forecast_issued_at_utc,
        air.forecast_issued_at_utc is not distinct from weather.forecast_issued_at_utc
            as is_vintage_aligned,
        air.valid_at_utc,
        timezone('Asia/Ho_Chi_Minh', air.valid_at_utc) as valid_at_local,
        date_diff('hour', air.forecast_issued_at_utc, air.valid_at_utc) as lead_hours,
        air.pm25_ugm3,
        air.pm10_ugm3,
        air.no2_ugm3,
        air.o3_ugm3,
        air.so2_ugm3,
        air.co_ugm3,
        weather.temperature_2m_c,
        weather.apparent_temperature_c,
        weather.relative_humidity_2m_pct,
        weather.precipitation_probability_pct,
        weather.precipitation_mm,
        weather.wind_speed_10m_kmh,
        weather.wind_direction_10m_deg,
        weather.uv_index,
        air.air_quality_source,
        weather.source_name as weather_source,
        air.resolution_note,
        'modeled' as source_type,
        'MODELED_ONLY' as coverage_tier
    from air_pivot as air
    inner join {{ ref('dim_province') }} as province
        on air.province_code = province.province_code
    left join weather_current as weather
        on air.location_key = weather.location_key
        and air.valid_at_utc = weather.valid_at_utc
),

scored as (
    select
        *,
        least(70.0, coalesce(pm25_ugm3, 50.0) * 1.35) as air_penalty,
        least(15.0, coalesce(precipitation_probability_pct, 0.0) * 0.15) as rain_penalty,
        case
            when apparent_temperature_c > 36 then least(15.0, (apparent_temperature_c - 36) * 3)
            when apparent_temperature_c < 15 then least(15.0, (15 - apparent_temperature_c) * 2)
            else 0.0
        end as temperature_penalty,
        greatest(0.0, coalesce(uv_index, 0.0) - 5.0) * 2.0 as uv_penalty
    from joined
)

select
    *,
    round(
        greatest(0.0, 100.0 - air_penalty - rain_penalty - temperature_penalty - uv_penalty),
        1
    ) as outdoor_score,
    -- Mixed vintages degrade confidence: the weather and air inputs to the score
    -- then describe different model runs even though each is internally whole.
    case
        when pm25_ugm3 is null or temperature_2m_c is null then 'LOW'
        when not is_vintage_aligned then 'LOW'
        when lead_hours <= 24 then 'MEDIUM'
        else 'LOW'
    end as confidence_level,
    case
        when 100.0 - air_penalty - rain_penalty - temperature_penalty - uv_penalty >= 70
            then 'Phù hợp hơn'
        when 100.0 - air_penalty - rain_penalty - temperature_penalty - uv_penalty >= 45
            then 'Cân nhắc'
        else 'Nên hạn chế'
    end as decision_label,
    concat(
        'PM2.5 ', round(coalesce(pm25_ugm3, 0), 1), ' µg/m³; ',
        'mưa ', round(coalesce(precipitation_probability_pct, 0), 0), '%; ',
        'cảm nhận ', round(coalesce(apparent_temperature_c, temperature_2m_c), 1), ' °C'
    ) as decision_explanation
from scored
