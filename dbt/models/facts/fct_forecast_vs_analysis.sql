-- Pairs each forecast hour with the same model's own analysis of that hour, at the
-- same coordinate. This is the term finding O was missing.
--
-- The gap the product already publishes, forecast-at-anchor versus station, contains
-- three things added together:
--
--   F_anchor - O_station = (F_anchor - M_anchor)   forecast drift, measured here
--                        + (M_anchor - M_station)  representativeness, not yet built
--                        + (M_station - O_station) model against reality, not yet built
--
-- That is an identity, not a model: the two middle terms cancel. This relation
-- measures the first one, and it is the only one of the three that says anything
-- about forecasting. The other two are spatial and instrumental.
--
-- **What a small drift does NOT mean.** Agreeing with your own analysis is not the
-- same as being right. If CAMS reads 3.9x the station at that point, a forecast that
-- reproduces CAMS reads 3.9x the station too. A near-1.0 ratio here excludes the
-- forecast as the *explanation* for the published gap; it establishes nothing
-- whatsoever about accuracy, and no downstream surface may present it as if it did.
-- That is a sharper way to overclaim than the one this project already guards
-- against, because 1.0 looks like good news.
--
-- Three conditions make the comparison legitimate, each verified rather than assumed:
--
-- 1. Same coordinate. Forecasts are fetched at the province anchor from the seed;
--    historical model values are fetched at CITIES[key] in Python. Those are two
--    separate registries that happen to agree, and if they ever stop agreeing this
--    term silently acquires a spatial component that no SQL here could reveal.
--    tests/test_decomposition_assumptions.py fails when they diverge.
-- 2. Same product. Both come from Open-Meteo's air-quality endpoint; the historical
--    call passes start_date/end_date and the forecast call passes forecast_hours.
-- 3. Same unit. Both sides are µg/m³ and a dbt test asserts no row pairs two units.
--
-- Restricted to the three pilot cities, because those are the only locations with a
-- historical modelled series to compare against. Da Nang is included even though it
-- has no station: this term needs no observation, so drift is measurable there, and a
-- location where drift is small while no station exists is still evidence about the
-- forecast rather than about the city.
--
-- An analysis arrives later, exactly as an observation does. The daily DAG loads a
-- whole past UTC day of modelled history, so a forecast issued for tomorrow cannot be
-- compared with anything until that day lands. The three-state vocabulary and the
-- 168-hour cutoff are therefore taken unchanged from fct_forecast_verification: two
-- relations that describe the same waiting should not describe it differently.
--
-- Left join, for the same reason. An inner join would make a forecast hour whose
-- analysis has not arrived indistinguishable from one that can never have an
-- analysis, and would leave this relation empty on any warehouse whose forecast
-- horizon sits entirely in the future -- which is exactly what the CI fixture is.
--
-- A view, matching fct_forecast_verification: the classification below moves with the
-- clock, and a table would freeze it at build time and then age in silence.
{{ config(materialized='view') }}

{% set unpairable_hours = 168 %}

with as_of as (
    select current_timestamp as as_of_utc
),

forecast as (
    select
        location_key,
        pollutant,
        forecast_issued_at_utc,
        valid_at_utc,
        concentration as forecast_concentration,
        unit as forecast_unit
    from {{ ref('fct_air_quality_forecast') }}
),

analysis as (
    select
        city_key as location_key,
        pollutant,
        observed_at_utc as valid_at_utc,
        concentration as analysis_concentration,
        unit as analysis_unit
    from {{ ref('mart_city_air_quality_hourly') }}
    where source_type = 'modeled'
),

-- Only the locations that have a modelled history at all. Thirty-one of the
-- thirty-four anchors have none, and emitting PENDING rows for them would
-- manufacture hours that can never resolve -- letting a reader mistake "not
-- comparable here" for "not compared yet", which are different sentences.
comparable_series as (
    select distinct location_key, pollutant
    from analysis
),

paired as (
    select
        forecast.location_key,
        forecast.pollutant,
        forecast.forecast_issued_at_utc,
        forecast.valid_at_utc,
        forecast.forecast_concentration,
        analysis.analysis_concentration,
        forecast.forecast_unit,
        analysis.analysis_unit,
        as_of.as_of_utc
    from forecast
    inner join comparable_series
        on forecast.location_key = comparable_series.location_key
        and forecast.pollutant = comparable_series.pollutant
    cross join as_of
    left join analysis
        on forecast.location_key = analysis.location_key
        and forecast.pollutant = analysis.pollutant
        and forecast.valid_at_utc = analysis.valid_at_utc
)

select
    location_key,
    pollutant,
    forecast_issued_at_utc,
    valid_at_utc,
    as_of_utc,
    forecast_unit as unit,
    analysis_unit,
    cast(date_diff('hour', forecast_issued_at_utc, valid_at_utc) as integer) as lead_hours,
    -- The same three bands fct_forecast_verification uses, so the two relations can be
    -- read side by side. Per lead hour the sample is far too thin to say anything.
    case
        when date_diff('hour', forecast_issued_at_utc, valid_at_utc) <= 24 then '1-24h'
        when date_diff('hour', forecast_issued_at_utc, valid_at_utc) <= 48 then '25-48h'
        else '49-72h'
    end as lead_band,
    forecast_concentration,
    analysis_concentration,
    case
        when analysis_concentration is not null then 'PAIRED'
        when valid_at_utc > as_of_utc - interval '{{ unpairable_hours }} hours' then 'PENDING'
        else 'UNPAIRABLE'
    end as pairing_status,
    -- Drift, not error. The forecast and the analysis are two statements by the same
    -- model about the same hour and place; neither is ground truth, so neither can be
    -- called wrong relative to the other. Signed and absolute stay apart because a
    -- consistent offset is a different finding from an imprecise one.
    case
        when analysis_concentration is not null
            then forecast_concentration - analysis_concentration
    end as drift_ugm3,
    case
        when analysis_concentration is not null
            then abs(forecast_concentration - analysis_concentration)
    end as abs_drift_ugm3
from paired
