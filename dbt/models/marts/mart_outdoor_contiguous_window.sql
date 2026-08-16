-- One row is one contiguous outdoor window for one location and duration.
--
-- A two-hour window means two adjacent forecast hours both satisfy the score
-- band represented by the window's decision_label. It is never the best two
-- arbitrary hours. The same rule applies to the three-hour window.
--
-- window_score is the worst member-hour score, not the average. A mean would
-- let one very bad hour hide behind a very good one, even though somebody going
-- outside experiences both hours. Reusing the existing 70/45 decision-label
-- cut points therefore means every member hour reaches the window's label.
--
-- An hour missing any input used by outdoor_score is removed before sequencing.
-- The explicit +1h/+2h predicates below then break the window at that gap:
-- missing data is not a good condition and cannot be jumped over as if present.
--
-- Materialized as a view because the 72-hour horizon is anchored on
-- current_timestamp and must keep moving between dbt builds.
{{ config(materialized='view') }}

with as_of as (
    select current_timestamp as as_of_utc
),

eligible_hours as (
    select forecast.*
    from {{ ref('mart_location_hourly_forecast') }} as forecast
    cross join as_of
    where forecast.valid_at_utc >= date_trunc('hour', as_of.as_of_utc)
      and forecast.valid_at_utc < date_trunc('hour', as_of.as_of_utc) + interval '72 hours'
      and forecast.pm25_ugm3 is not null
      and forecast.temperature_2m_c is not null
      and forecast.apparent_temperature_c is not null
      and forecast.precipitation_probability_pct is not null
      and forecast.uv_index is not null
),

sequenced as (
    select
        *,
        lead(valid_at_utc, 1) over location_hours as second_hour_utc,
        lead(valid_at_utc, 2) over location_hours as third_hour_utc,
        lead(outdoor_score, 1) over location_hours as second_hour_score,
        lead(outdoor_score, 2) over location_hours as third_hour_score,
        lead(confidence_level, 1) over location_hours as second_hour_confidence,
        lead(confidence_level, 2) over location_hours as third_hour_confidence
    from eligible_hours
    window location_hours as (partition by location_key order by valid_at_utc)
),

two_hour_windows as (
    select
        location_key,
        province_code,
        province_name,
        unit_type,
        anchor_name,
        forecast_issued_at_utc,
        weather_forecast_issued_at_utc,
        is_vintage_aligned,
        valid_at_utc as window_start_utc,
        valid_at_utc + interval '2 hours' as window_end_utc,
        timezone('Asia/Ho_Chi_Minh', valid_at_utc) as window_start_local,
        timezone('Asia/Ho_Chi_Minh', valid_at_utc + interval '2 hours') as window_end_local,
        2 as duration_hours,
        valid_at_utc as first_hour_utc,
        second_hour_utc,
        cast(null as timestamp with time zone) as third_hour_utc,
        outdoor_score as first_hour_score,
        second_hour_score,
        cast(null as double) as third_hour_score,
        least(outdoor_score, second_hour_score) as window_score,
        case
            when outdoor_score <= second_hour_score then valid_at_utc
            else second_hour_utc
        end as worst_hour_utc,
        case
            when confidence_level = 'LOW' or second_hour_confidence = 'LOW' then 'LOW'
            else 'MEDIUM'
        end as confidence_level,
        coverage_tier
    from sequenced
    where second_hour_utc = valid_at_utc + interval '1 hour'
),

three_hour_windows as (
    select
        location_key,
        province_code,
        province_name,
        unit_type,
        anchor_name,
        forecast_issued_at_utc,
        weather_forecast_issued_at_utc,
        is_vintage_aligned,
        valid_at_utc as window_start_utc,
        valid_at_utc + interval '3 hours' as window_end_utc,
        timezone('Asia/Ho_Chi_Minh', valid_at_utc) as window_start_local,
        timezone('Asia/Ho_Chi_Minh', valid_at_utc + interval '3 hours') as window_end_local,
        3 as duration_hours,
        valid_at_utc as first_hour_utc,
        second_hour_utc,
        third_hour_utc,
        outdoor_score as first_hour_score,
        second_hour_score,
        third_hour_score,
        least(outdoor_score, second_hour_score, third_hour_score) as window_score,
        case
            when outdoor_score <= second_hour_score and outdoor_score <= third_hour_score
                then valid_at_utc
            when second_hour_score <= third_hour_score then second_hour_utc
            else third_hour_utc
        end as worst_hour_utc,
        case
            when confidence_level = 'LOW'
                or second_hour_confidence = 'LOW'
                or third_hour_confidence = 'LOW'
                then 'LOW'
            else 'MEDIUM'
        end as confidence_level,
        coverage_tier
    from sequenced
    where second_hour_utc = valid_at_utc + interval '1 hour'
      and third_hour_utc = valid_at_utc + interval '2 hours'
),

windows as (
    select * from two_hour_windows
    union all by name
    select * from three_hour_windows
),

labeled as (
    select
        *,
        timezone('Asia/Ho_Chi_Minh', worst_hour_utc) as worst_hour_local,
        case
            when window_score >= 70 then 'Phù hợp hơn'
            when window_score >= 45 then 'Cân nhắc'
            else 'Nên hạn chế'
        end as decision_label
    from windows
)

select
    *,
    row_number() over (
        partition by location_key, duration_hours
        order by window_score desc, window_start_utc
    ) as suitability_rank
from labeled
