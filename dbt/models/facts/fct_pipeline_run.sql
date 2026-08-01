-- Grain: one row per pipeline run_id and target UTC data date.
-- Reruns of the same date merge on the same key, so the audit trail stays
-- idempotent alongside the measurement tables.
select
    run_id,
    data_date_utc,
    started_at_utc,
    finished_at_utc,
    duration_seconds,
    raw_backend,
    include_openaq,
    raw_objects,
    weather_rows,
    observed_air_quality_rows,
    modeled_air_quality_rows,
    weather_rows + observed_air_quality_rows + modeled_air_quality_rows as total_rows,
    pipeline_version,
    row_number() over (
        partition by data_date_utc order by finished_at_utc desc
    ) = 1 as is_latest_run_for_date
from {{ ref('stg_pipeline_runs') }}
