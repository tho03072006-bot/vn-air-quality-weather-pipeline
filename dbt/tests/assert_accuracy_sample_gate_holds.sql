-- No error figure may be published from a sample too small to support it, and no
-- adequate sample may be silently withheld.
--
-- Both directions are asserted on purpose. A gate that only checks one of them can
-- be satisfied by publishing nothing at all, which would pass while telling the
-- reader nothing -- the same shape as a check that cannot fail.
--
-- The arithmetic clause is the one that matters most in practice: paired hours must
-- equal the hours that actually carry an error. If they ever diverge, the divisor
-- behind every mean on this page is not the number printed beside it.
with accuracy as (
    select
        location_key,
        pollutant,
        lead_band,
        paired_hours,
        pending_hours,
        unverifiable_hours,
        min_paired_hours,
        has_sufficient_sample,
        mae_ugm3,
        rmse_ugm3,
        bias_ugm3
    from {{ ref('mart_forecast_accuracy') }}
),

verified_counts as (
    select
        location_key,
        pollutant,
        lead_band,
        count_if(abs_error_ugm3 is not null) as hours_with_error,
        count(*) as total_hours
    from {{ ref('fct_forecast_verification') }}
    group by location_key, pollutant, lead_band
),

violations as (
    select 'metric published below the sample floor' as violation, count(*) as rows
    from accuracy
    where paired_hours < min_paired_hours
      and (mae_ugm3 is not null or rmse_ugm3 is not null or bias_ugm3 is not null)

    union all

    select 'metric withheld despite an adequate sample', count(*)
    from accuracy
    where paired_hours >= min_paired_hours
      and (mae_ugm3 is null or rmse_ugm3 is null or bias_ugm3 is null)

    union all

    select 'has_sufficient_sample disagrees with the count', count(*)
    from accuracy
    where has_sufficient_sample is distinct from (paired_hours >= min_paired_hours)

    union all

    select 'paired_hours is not the number of hours carrying an error', count(*)
    from accuracy
    join verified_counts using (location_key, pollutant, lead_band)
    where accuracy.paired_hours <> verified_counts.hours_with_error

    union all

    select 'the three states do not account for every hour', count(*)
    from accuracy
    join verified_counts using (location_key, pollutant, lead_band)
    where accuracy.paired_hours + accuracy.pending_hours + accuracy.unverifiable_hours
        <> verified_counts.total_hours
)

select violation, rows
from violations
where rows > 0
