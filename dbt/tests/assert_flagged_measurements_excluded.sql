-- Recomputes the published concentration from the fact using unflagged rows only
-- and fails if the mart disagrees.
--
-- This is the test that catches a future refactor quietly letting flagged readings
-- back into the average. A test that only checked excluded_flagged_count would
-- still pass in that case, because the count would keep reporting the flagged rows
-- while the average silently included them again -- which is exactly the bug this
-- policy replaced.
--
-- Also asserts every published row rests on at least one unflagged reading, so an
-- hour that was entirely rejected cannot reappear with a NULL concentration that
-- coverage would count as data.
with expected as (
    select
        city_key,
        date_trunc('hour', observed_at_utc) as observed_at_utc,
        pollutant,
        unit,
        source_name,
        avg(concentration) as concentration
    from {{ ref('fct_air_quality_hourly') }}
    where not flagged
    group by 1, 2, 3, 4, 5
),

disagreements as (
    select
        published.city_key,
        published.observed_at_utc,
        published.pollutant,
        published.source_name,
        published.concentration as published_concentration,
        expected.concentration as unflagged_only_concentration
    from {{ ref('mart_city_air_quality_hourly') }} as published
    inner join expected
        on published.city_key = expected.city_key
        and published.observed_at_utc = expected.observed_at_utc
        and published.pollutant = expected.pollutant
        and published.unit = expected.unit
        and published.source_name = expected.source_name
    where abs(published.concentration - expected.concentration) > 1e-9
),

unsupported as (
    select
        city_key,
        observed_at_utc,
        pollutant,
        source_name,
        concentration as published_concentration,
        cast(null as double) as unflagged_only_concentration
    from {{ ref('mart_city_air_quality_hourly') }}
    where included_measurement_count <= 0
       or concentration is null
)

select * from disagreements
union all
select * from unsupported
