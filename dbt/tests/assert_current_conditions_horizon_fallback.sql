-- Every configured anchor must stay represented in current conditions, before and
-- after its 72-hour horizon expires.
--
-- Before this test the serving view filtered expired hours out before ranking, so
-- once the horizon elapsed it returned zero rows for every location at once. A
-- reader could not tell that from "this location never had data", and no test
-- noticed because the fixture could not build an exhausted horizon at all. Build
-- one with `--forecast-age-hours 96` to exercise the fallback arm.
--
-- Both arms are asserted, because a fallback that fires when it should not is the
-- same defect wearing the other face: an exhausted anchor must serve its final
-- hour, and a healthy anchor must still serve the hour nearest to now.
with expected_locations as (
    select location_key
    from {{ ref('dim_location') }}
),

actual as (
    select
        location_key,
        valid_at_utc,
        as_of_utc,
        forecast_horizon_end_utc,
        is_forecast_horizon_exhausted
    from {{ ref('mart_current_conditions') }}
)

select
    expected.location_key
from expected_locations as expected
left join actual
    on expected.location_key = actual.location_key
-- The anchor vanished entirely. This is the arm that fails if the pre-filter
-- comes back: it removes every candidate, so every location disappears at once.
where actual.location_key is null
    or actual.forecast_horizon_end_utc is null
    or actual.is_forecast_horizon_exhausted is null
    -- Exhausted: the served row must be the last hour the horizon ever covered,
    -- not an arbitrary surviving one.
    or (
        actual.is_forecast_horizon_exhausted
        and actual.valid_at_utc <> actual.forecast_horizon_end_utc
    )
    -- Healthy: the served row must still be the hour nearest to now. An hourly
    -- grid puts that within 30 minutes; one hour leaves margin without letting a
    -- broken ranking through.
    or (
        not actual.is_forecast_horizon_exhausted
        and abs(epoch(actual.valid_at_utc - actual.as_of_utc)) > 3600
    )
