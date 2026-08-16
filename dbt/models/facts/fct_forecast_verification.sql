-- Pairs each forecast hour with the observation that later validated it.
--
-- This is the fact the whole project has been deferring to. Every surface says
-- "confidence" rather than "accuracy" because no empirical comparison existed, and
-- verify_streamlit.py asserts the Trust page keeps saying so. Nothing here changes
-- that wording; it builds the measurement that would eventually justify changing it.
--
-- Materialised as a view, for the same reason mart_current_conditions is one
-- (audit register, finding E): the classification below depends on the current
-- clock. A row that is PENDING today becomes VERIFIED or UNVERIFIABLE later, and a
-- table would freeze that judgement at build time and then age in silence.
--
-- Three deliberate choices, each of which could quietly corrupt the numbers:
--
-- 1. It joins mart_city_air_quality_hourly, NOT fct_air_quality_hourly. The fact is
--    at station grain and includes provider-flagged readings; the mart is one row
--    per city/pollutant/hour with flagged readings already excluded (finding B).
--    Verifying against data the project refuses to publish would compare a forecast
--    to a number no reader ever sees.
--
-- 2. Only series that actually have observations are represented at all. Thirty-two
--    of the thirty-four province anchors have no OpenAQ coverage, and Da Nang has
--    none either. Emitting PENDING rows for them would manufacture millions of
--    hours that can never resolve, and would let a reader mistake "not measurable
--    here" for "not measured yet". Those are different sentences.
--
-- 3. lead_hours is measured from forecast_issued_at_utc, which is the time this
--    system FETCHED the forecast, not the provider's model-run time -- the API does
--    not return the latter (limitations table, item 3). So lead time here is a lower
--    bound on true model lead time, and must never be described as the latter.
{{ config(materialized='view') }}

{#
  The point at which waiting stops and a missing observation becomes a permanent gap
  rather than a late arrival.

  One threshold, not two. The design called for a 48-hour "too early to judge" state
  alongside this one, on the reasoning that the daily DAG lands a whole UTC day at
  once roughly 26 hours after its earliest hour. Writing it out showed that threshold
  classifies nothing: a row with an observation is VERIFIED at any age, and a row
  without one is excluded from the sample whether it is ten hours old or a hundred.
  A second state that changes no row and drives no decision is complexity pretending
  to be rigour, so it is not here.

  168 hours is deliberately generous, and measured behaviour is why: this warehouse
  has seen its host offline for 32 hours, and a backfill that landed five days of
  observations in a single burst. A tighter cutoff would convert an outage into a
  permanent hole in the accuracy record -- and, worse, into a silent one.
#}
{% set unverifiable_hours = 168 %}

with as_of as (
    select current_timestamp as as_of_utc
),

-- Which (location, pollutant) series can ever be verified. Derived from the data
-- rather than listed, so a newly covered station starts being verified without a
-- code change, and a station that goes away stops silently claiming coverage.
observable_series as (
    select distinct
        city_key as location_key,
        pollutant
    from {{ ref('mart_city_air_quality_hourly') }}
    where source_type = 'observed'
),

forecast as (
    select
        f.location_key,
        f.pollutant,
        f.forecast_issued_at_utc,
        f.valid_at_utc,
        f.concentration as forecast_concentration,
        f.unit
    from {{ ref('fct_air_quality_forecast') }} as f
    inner join observable_series as s
        on f.location_key = s.location_key
        and f.pollutant = s.pollutant
),

observed as (
    select
        city_key as location_key,
        pollutant,
        observed_at_utc,
        concentration as observed_concentration
    from {{ ref('mart_city_air_quality_hourly') }}
    where source_type = 'observed'
),

paired as (
    select
        forecast.location_key,
        forecast.pollutant,
        forecast.forecast_issued_at_utc,
        forecast.valid_at_utc,
        forecast.forecast_concentration,
        forecast.unit,
        observed.observed_concentration,
        as_of.as_of_utc
    from forecast
    cross join as_of
    left join observed
        on forecast.location_key = observed.location_key
        and forecast.pollutant = observed.pollutant
        and forecast.valid_at_utc = observed.observed_at_utc
)

select
    location_key,
    pollutant,
    forecast_issued_at_utc,
    valid_at_utc,
    as_of_utc,
    cast(date_diff('hour', forecast_issued_at_utc, valid_at_utc) as integer) as lead_hours,
    -- Banded rather than per-hour. Per lead hour the sample is far too thin to say
    -- anything: measured on this warehouse, the 49-72h range holds roughly eighty
    -- paired hours per series in total, which spread across twenty-four individual
    -- hours is under four each.
    case
        when date_diff('hour', forecast_issued_at_utc, valid_at_utc) <= 24 then '1-24h'
        when date_diff('hour', forecast_issued_at_utc, valid_at_utc) <= 48 then '25-48h'
        else '49-72h'
    end as lead_band,
    forecast_concentration,
    observed_concentration,
    unit,
    case
        when observed_concentration is not null then 'VERIFIED'
        when valid_at_utc > as_of_utc - interval '{{ unverifiable_hours }} hours' then 'PENDING'
        else 'UNVERIFIABLE'
    end as verification_status,
    -- Signed. Positive means the forecast was higher than the observation. Kept
    -- separate from the absolute error because a model that is consistently high is
    -- a different problem from one that is merely imprecise, and averaging absolute
    -- errors hides which one this is.
    case
        when observed_concentration is not null
            then forecast_concentration - observed_concentration
    end as error_ugm3,
    case
        when observed_concentration is not null
            then abs(forecast_concentration - observed_concentration)
    end as abs_error_ugm3
from paired
