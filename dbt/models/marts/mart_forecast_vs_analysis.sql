-- How far the forecast drifts from the same model's own analysis, by location,
-- pollutant and lead band.
--
-- Read this beside mart_model_station_discrepancy, never instead of it. That mart
-- measures forecast-at-anchor against a street-level station and cannot separate
-- model error from representativeness error. This one removes the spatial question
-- entirely by comparing the model with itself at one point, and what remains is the
-- only part of the published gap that is about forecasting at all.
--
-- Measured on this warehouse the first time it ran: Hanoi ozone drift sits at
-- 1.07 / 0.95 / 1.03 across the three lead bands while the published model-station
-- ratio for the same series is 4.67 / 4.75 / 4.77. The forecast reproduces the
-- analysis; the gap to the station is somewhere else.
--
-- **A drift near 1.0 is not an accuracy result and must never be published as one.**
-- The forecast and the analysis are the same model twice. If that model reads 3.9x
-- the station at that coordinate, a faithful forecast reads 3.9x the station too.
-- What this excludes is forecasting as the explanation for the gap. What it leaves
-- open is everything about whether either number is right.
--
-- The shape across lead bands is the discriminating evidence, and it is why the bands
-- are kept rather than averaged away. Real forecast drift must grow with lead time,
-- because predicting further ahead is harder. A term that is flat across 1-24h,
-- 25-48h and 49-72h is not forecast behaviour, and the published 4.7x is flat.
--
-- Sample gating, wording and NULL semantics deliberately match
-- mart_model_station_discrepancy: the two tables appear together on the Trust page,
-- and a reader who learns that an em dash means "not enough evidence" in one of them
-- should not have to learn a second convention for the other.
--
-- A view, because the fact beneath it is one.
{{ config(materialized='view') }}

{#
  The same floor mart_model_station_discrepancy uses, for the same reason and with
  the same consequence: below it, publish nothing rather than a number built from a
  handful of hours. Kept as a separate literal rather than imported, because a shared
  constant that silently changes both tables at once is harder to reason about than
  two numbers a reader can compare.
#}
{% set min_paired_hours = 30 %}

with drift as (
    select *
    from {{ ref('fct_forecast_vs_analysis') }}
),

aggregated as (
    select
        location_key,
        pollutant,
        lead_band,
        max(unit) as unit,
        max(as_of_utc) as as_of_utc,
        count_if(pairing_status = 'PAIRED') as paired_hours,
        count_if(pairing_status = 'PENDING') as pending_hours,
        count_if(pairing_status = 'UNPAIRABLE') as unpairable_hours,
        count(distinct forecast_issued_at_utc) as vintages,
        min(valid_at_utc) as first_valid_at_utc,
        max(valid_at_utc) as last_valid_at_utc,
        avg(abs_drift_ugm3) as mean_abs_raw,
        sqrt(avg(drift_ugm3 * drift_ugm3)) as rms_raw,
        avg(drift_ugm3) as mean_signed_raw,
        avg(forecast_concentration) as mean_forecast_raw,
        avg(analysis_concentration) as mean_analysis_raw
    from drift
    group by location_key, pollutant, lead_band
)

select
    location_key,
    pollutant,
    lead_band,
    unit,
    as_of_utc,
    paired_hours,
    pending_hours,
    unpairable_hours,
    vintages,
    first_valid_at_utc,
    last_valid_at_utc,
    paired_hours >= {{ min_paired_hours }} as has_sufficient_sample,
    {{ min_paired_hours }} as min_paired_hours,
    -- Gated to NULL rather than rounded down, so a consumer rendering an em dash is
    -- telling the truth and one rendering a number from nine hours is not.
    case when paired_hours >= {{ min_paired_hours }} then mean_abs_raw end
        as mean_abs_drift_ugm3,
    case when paired_hours >= {{ min_paired_hours }} then rms_raw end
        as rms_drift_ugm3,
    -- Signed. Positive means the forecast read higher than the later analysis.
    case when paired_hours >= {{ min_paired_hours }} then mean_signed_raw end
        as mean_signed_drift_ugm3,
    case when paired_hours >= {{ min_paired_hours }} then mean_forecast_raw end
        as mean_forecast_ugm3,
    case when paired_hours >= {{ min_paired_hours }} then mean_analysis_raw end
        as mean_analysis_ugm3
from aggregated
