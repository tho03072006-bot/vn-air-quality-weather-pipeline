-- No discrepancy figure may be published from a sample too small to support it, and
-- no adequate sample may be silently withheld.
--
-- Both directions are asserted on purpose. A gate that only rejects over-publishing
-- can be satisfied by publishing nothing at all, which passes while telling the
-- reader nothing -- the same shape as a check that cannot fail.
--
-- The arithmetic clauses matter most in practice. paired_hours has to equal the
-- hours that actually carry a discrepancy, or the divisor behind every mean is not
-- the number printed beside it; and the three states have to account for every hour,
-- or some hours are being dropped by neither rule.
with discrepancy as (
    select
        location_key,
        pollutant,
        lead_band,
        paired_hours,
        pending_hours,
        unverifiable_hours,
        min_paired_hours,
        has_sufficient_sample,
        mean_abs_discrepancy_ugm3,
        rms_discrepancy_ugm3,
        mean_signed_discrepancy_ugm3
    from {{ ref('mart_model_station_discrepancy') }}
),

verified_counts as (
    select
        location_key,
        pollutant,
        lead_band,
        count_if(abs_discrepancy_ugm3 is not null) as hours_with_discrepancy,
        count(*) as total_hours
    from {{ ref('fct_forecast_verification') }}
    group by location_key, pollutant, lead_band
),

violations as (
    select 'metric published below the sample floor' as violation, count(*) as rows
    from discrepancy
    where paired_hours < min_paired_hours
      and (
          mean_abs_discrepancy_ugm3 is not null
          or rms_discrepancy_ugm3 is not null
          or mean_signed_discrepancy_ugm3 is not null
      )

    union all

    select 'metric withheld despite an adequate sample', count(*)
    from discrepancy
    where paired_hours >= min_paired_hours
      and (
          mean_abs_discrepancy_ugm3 is null
          or rms_discrepancy_ugm3 is null
          or mean_signed_discrepancy_ugm3 is null
      )

    union all

    select 'has_sufficient_sample disagrees with the count', count(*)
    from discrepancy
    where has_sufficient_sample is distinct from (paired_hours >= min_paired_hours)

    union all

    select 'paired_hours is not the number of hours carrying a discrepancy', count(*)
    from discrepancy
    join verified_counts using (location_key, pollutant, lead_band)
    where discrepancy.paired_hours <> verified_counts.hours_with_discrepancy

    union all

    select 'the three states do not account for every hour', count(*)
    from discrepancy
    join verified_counts using (location_key, pollutant, lead_band)
    where discrepancy.paired_hours + discrepancy.pending_hours + discrepancy.unverifiable_hours
        <> verified_counts.total_hours
)

select violation, rows
from violations
where rows > 0
