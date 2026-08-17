-- The same two-directional gate assert_discrepancy_sample_gate_holds applies to the
-- discrepancy mart, applied to the drift mart for the same reasons.
--
-- Both directions, because a gate that only rejects over-publishing is satisfied by
-- publishing nothing at all -- which passes while telling the reader nothing, the
-- shape of a check that cannot fail.
--
-- The arithmetic clause is the one that matters in practice: paired_hours has to be
-- the number of rows actually averaged, or the divisor behind every mean is not the
-- number printed beside it.
with drift as (
    select
        location_key,
        pollutant,
        lead_band,
        paired_hours,
        pending_hours,
        unpairable_hours,
        min_paired_hours,
        has_sufficient_sample,
        mean_abs_drift_ugm3,
        rms_drift_ugm3,
        mean_signed_drift_ugm3
    from {{ ref('mart_forecast_vs_analysis') }}
),

fact_counts as (
    select
        location_key,
        pollutant,
        lead_band,
        count_if(abs_drift_ugm3 is not null) as hours_with_drift,
        count(*) as total_hours
    from {{ ref('fct_forecast_vs_analysis') }}
    group by location_key, pollutant, lead_band
),

violations as (
    select 'metric published below the sample floor' as violation, count(*) as rows
    from drift
    where paired_hours < min_paired_hours
      and (
          mean_abs_drift_ugm3 is not null
          or rms_drift_ugm3 is not null
          or mean_signed_drift_ugm3 is not null
      )

    union all

    select 'metric withheld despite an adequate sample', count(*)
    from drift
    where paired_hours >= min_paired_hours
      and (
          mean_abs_drift_ugm3 is null
          or rms_drift_ugm3 is null
          or mean_signed_drift_ugm3 is null
      )

    union all

    select 'has_sufficient_sample disagrees with the count', count(*)
    from drift
    where has_sufficient_sample is distinct from (paired_hours >= min_paired_hours)

    union all

    select 'paired_hours is not the number of hours carrying a drift', count(*)
    from drift
    join fact_counts using (location_key, pollutant, lead_band)
    where drift.paired_hours <> fact_counts.hours_with_drift

    union all

    select 'the three states do not account for every hour', count(*)
    from drift
    join fact_counts using (location_key, pollutant, lead_band)
    where drift.paired_hours + drift.pending_hours + drift.unpairable_hours
        <> fact_counts.total_hours
)

select violation, rows
from violations
where rows > 0
