-- Grain: one row per pipeline run_id and target UTC data date.
-- Reruns of the same date merge on the same key, so the audit trail stays
-- idempotent alongside the measurement tables.
select
    run_id,
    pipeline_name,
    data_date_utc,
    status,
    started_at_utc,
    finished_at_utc,
    duration_seconds,
    raw_backend,
    include_openaq,
    raw_objects_attempted,
    raw_objects_created,
    raw_objects_reused,
    requested_location_count,
    succeeded_location_count,
    failed_location_count,
    weather_rows,
    observed_air_quality_rows,
    modeled_air_quality_rows,
    weather_forecast_rows,
    air_quality_forecast_rows,
    weather_rows + observed_air_quality_rows + modeled_air_quality_rows as total_rows,
    weather_forecast_rows + air_quality_forecast_rows as total_forecast_rows,
    error_category,
    error_summary,
    -- Partitioned by pipeline_name as well as date. Without it the historical and
    -- forecast runs that share a date compete for one "latest" flag, so whichever
    -- finished second silently hides the other from the health panel.
    row_number() over (
        partition by pipeline_name, data_date_utc order by finished_at_utc desc
    ) = 1 as is_latest_run_for_date
from {{ ref('stg_pipeline_runs') }}
