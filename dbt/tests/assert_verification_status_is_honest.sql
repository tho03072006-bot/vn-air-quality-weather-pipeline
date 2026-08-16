-- The three verification states must mean exactly what they say, because every
-- error figure downstream is computed by trusting them.
--
-- Each clause below names a way the classification could be wrong while still
-- looking plausible in a spot check:
--
-- 1. VERIFIED without an observation, or without an error -- a row counted in the
--    sample that contributes nothing real to it.
-- 2. PENDING or UNVERIFIABLE carrying an error -- an unobserved hour smuggling a
--    number into the average.
-- 3. UNVERIFIABLE that is not actually past the cutoff, or PENDING that is. Getting
--    this backwards would either abandon hours that were still arriving or wait
--    forever on hours that never will.
-- 4. A series with no observation coverage appearing at all. Thirty-two province
--    anchors have no station; emitting rows for them would let "cannot be measured
--    here" be read as "not measured yet".
with verification as (
    select
        location_key,
        pollutant,
        valid_at_utc,
        as_of_utc,
        observed_concentration,
        discrepancy_ugm3,
        abs_discrepancy_ugm3,
        verification_status
    from {{ ref('fct_forecast_verification') }}
),

observable_series as (
    select distinct city_key as location_key, pollutant
    from {{ ref('mart_city_air_quality_hourly') }}
    where source_type = 'observed'
),

violations as (
    select 'VERIFIED without observation or discrepancy' as violation, count(*) as rows
    from verification
    where verification_status = 'VERIFIED'
      and (observed_concentration is null or discrepancy_ugm3 is null or abs_discrepancy_ugm3 is null)

    union all

    select 'unobserved status carrying a discrepancy', count(*)
    from verification
    where verification_status in ('PENDING', 'UNVERIFIABLE')
      and (observed_concentration is not null or discrepancy_ugm3 is not null)

    union all

    -- 168 hours is the cutoff the model declares. Recomputing it from the row's own
    -- timestamps catches a threshold that drifts away from its documented value.
    select 'UNVERIFIABLE before the cutoff', count(*)
    from verification
    where verification_status = 'UNVERIFIABLE'
      and valid_at_utc > as_of_utc - interval '168 hours'

    union all

    select 'PENDING past the cutoff', count(*)
    from verification
    where verification_status = 'PENDING'
      and valid_at_utc <= as_of_utc - interval '168 hours'

    union all

    select 'row for a series with no observation coverage', count(*)
    from verification
    left join observable_series
        on verification.location_key = observable_series.location_key
        and verification.pollutant = observable_series.pollutant
    where observable_series.location_key is null
)

select violation, rows
from violations
where rows > 0
