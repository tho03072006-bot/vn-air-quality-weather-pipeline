-- Empirical forecast error by location, pollutant and lead band.
--
-- This is the first place in the project where a number may legitimately be called
-- error rather than confidence. It is deliberately hard to misread:
--
-- * Every figure carries `paired_hours` beside it. An error figure without its
--   sample size is an assertion, not a measurement.
-- * Below `min_paired_hours` the metrics are NULL rather than small-sample noise,
--   and `has_sufficient_sample` says so explicitly. The row still exists, because a
--   reader needs to learn that a series is being tracked but cannot yet be
--   characterised -- which is different from the series not existing.
-- * `pending_hours` and `unverifiable_hours` are published alongside. Excluding an
--   hour from the denominator is a decision, and a decision that cannot be reviewed
--   is indistinguishable from data loss (the same argument that put withheld
--   readings in mart_flagged_measurement_quarantine rather than deleting them).
--
-- What this model does NOT license: describing these numbers as the accuracy of the
-- product. Only two cities have any observations at all, so this characterises
-- Hanoi and Ho Chi Minh City. The other thirty-two province anchors have no station
-- within reach and are absent from this table entirely -- they are not "accurate",
-- they are unmeasured, and the UI must not let those read the same.
--
-- A view for the same reason its upstream fact is one: the PENDING/UNVERIFIABLE
-- split moves with the clock, and so therefore do these counts.
{{ config(materialized='view') }}

{#
  Below this, report nothing. Measured on this warehouse the thinnest band holds
  roughly eighty paired hours per series, so thirty is a floor that current data
  clears rather than a number chosen to make the table look full. It exists to stop
  a series that has just started being observed from publishing an error figure
  computed from a handful of hours.
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
        avg(abs_error_ugm3) as mae_raw,
        sqrt(avg(error_ugm3 * error_ugm3)) as rmse_raw,
        avg(error_ugm3) as bias_raw
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
    -- Gated, not rounded away. A NULL here means "not enough evidence to say", and
    -- a consumer that renders it as a blank or an em dash is telling the truth. A
    -- consumer that renders a number computed from nine hours is not.
    case when paired_hours >= {{ min_paired_hours }} then mae_raw end as mae_ugm3,
    case when paired_hours >= {{ min_paired_hours }} then rmse_raw end as rmse_ugm3,
    case when paired_hours >= {{ min_paired_hours }} then bias_raw end as bias_ugm3
from aggregated
