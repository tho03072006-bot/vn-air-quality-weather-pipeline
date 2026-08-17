-- How far the modelled forecast sits from the station that later measured the same
-- hour, by location, pollutant and lead band.
--
-- Named for what it measures, which is not forecast accuracy. The distinction is
-- the whole point of this model, so it is in the name rather than in a footnote a
-- reader can skip:
--
--   A forecast value is a CAMS grid cell at a province anchor. An observation is one
--   OpenAQ station, at one street, at one height. The gap between them contains
--   model error AND representativeness error, and nothing available here separates
--   the two. Calling the total "accuracy" would attribute all of it to the model.
--
-- The first run made that concrete rather than theoretical. At Hanoi the modelled
-- value ran 4.14x the station for ozone and 2.39x for PM2.5, high on 92% and 88% of
-- hours; units match on both sides, so it is not a conversion fault. Ho Chi Minh
-- City PM2.5 sat at 1.13x. A four-fold systematic offset is what two instruments
-- measuring different things looks like, not what a bad forecast looks like.
--
-- So this model publishes the discrepancy honestly and stops there.
--
-- One of the two unknowns has since been removed. mart_forecast_vs_analysis compares
-- the forecast with the same model's own analysis at the same coordinate, and measured
-- on this warehouse that drift sits near 1.0 with no growth across lead bands while
-- the ratio here is 4.7x and equally flat. So the gap below is not the forecast being
-- wrong. What is still not separated is the remainder: how much of it is a province
-- grid cell sitting somewhere other than the station, and how much is the model being
-- offset even at the right place. That needs the model sampled at the station's own
-- coordinates, and that work does not exist yet. Until it does, the UI keeps saying
-- confidence, and verify_streamlit.py keeps asserting that it does.
--
-- Every figure carries its sample size, and the excluded hours are published rather
-- than dropped, for the same reason withheld readings are quarantined instead of
-- deleted (finding B): an exclusion nobody can review is indistinguishable from data
-- loss.
--
-- A view, because the PENDING/UNVERIFIABLE split upstream moves with the clock and
-- so do these counts.
{{ config(materialized='view') }}

{#
  Below this, publish nothing. Measured here, the thinnest lead band holds about
  eighty paired hours per series, so thirty is a floor the current data clears rather
  than a number chosen to make the table look populated. It exists so a series that
  has only just started being observed cannot publish a figure built from a handful
  of hours.
#}
{% set min_paired_hours = 30 %}

with verification as (
    select *
    from {{ ref('fct_forecast_verification') }}
),

aggregated as (
    select
        location_key,
        pollutant,
        lead_band,
        max(as_of_utc) as as_of_utc,
        max(unit) as unit,
        count_if(verification_status = 'VERIFIED') as paired_hours,
        count_if(verification_status = 'PENDING') as pending_hours,
        count_if(verification_status = 'UNVERIFIABLE') as unverifiable_hours,
        count(distinct forecast_issued_at_utc) as vintages,
        min(valid_at_utc) as first_valid_at_utc,
        max(valid_at_utc) as last_valid_at_utc,
        avg(abs_discrepancy_ugm3) as mean_abs_raw,
        sqrt(avg(discrepancy_ugm3 * discrepancy_ugm3)) as rms_raw,
        avg(discrepancy_ugm3) as mean_signed_raw,
        avg(forecast_concentration) as mean_forecast_ugm3,
        avg(observed_concentration) as mean_observed_ugm3
    from verification
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
    unverifiable_hours,
    vintages,
    first_valid_at_utc,
    last_valid_at_utc,
    paired_hours >= {{ min_paired_hours }} as has_sufficient_sample,
    {{ min_paired_hours }} as min_paired_hours,
    -- Gated to NULL rather than rounded down. NULL means "not enough evidence to
    -- say", and a consumer rendering it as an em dash is telling the truth. One
    -- rendering a number computed from nine hours is not.
    case when paired_hours >= {{ min_paired_hours }} then mean_abs_raw end
        as mean_abs_discrepancy_ugm3,
    case when paired_hours >= {{ min_paired_hours }} then rms_raw end
        as rms_discrepancy_ugm3,
    -- Signed. Positive means the model reads higher than the station.
    case when paired_hours >= {{ min_paired_hours }} then mean_signed_raw end
        as mean_signed_discrepancy_ugm3,
    -- The two means side by side, because a ratio far from 1 is the signal that the
    -- gap is representativeness rather than forecast quality, and a reader cannot
    -- see that from a single averaged magnitude.
    case when paired_hours >= {{ min_paired_hours }} then mean_forecast_ugm3 end
        as mean_forecast_ugm3,
    case when paired_hours >= {{ min_paired_hours }} then mean_observed_ugm3 end
        as mean_observed_ugm3
from aggregated
