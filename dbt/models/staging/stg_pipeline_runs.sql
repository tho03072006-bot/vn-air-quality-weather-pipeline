-- Columns added after the first forecast run are coalesced rather than assumed
-- present: a warehouse loaded before the audit schema grew still has rows where
-- dlt never created them, and a NULL status would read as "no outcome" instead of
-- the success it actually was.
select
    cast(run_id as varchar) as run_id,
    cast(data_date as date) as data_date_utc,
    cast(started_at_utc as timestamptz) as started_at_utc,
    cast(finished_at_utc as timestamptz) as finished_at_utc,
    cast(duration_seconds as double) as duration_seconds,
    cast(raw_backend as varchar) as raw_backend,
    coalesce(cast(include_openaq as boolean), false) as include_openaq,
    -- Coalesced so that total_rows downstream can never be NULL and break the
    -- dashboard's freshness panel.
    coalesce(cast(raw_objects as integer), 0) as raw_objects_attempted,
    coalesce(cast(weather_rows as integer), 0) as weather_rows,
    coalesce(cast(observed_air_quality_rows as integer), 0) as observed_air_quality_rows,
    coalesce(cast(modeled_air_quality_rows as integer), 0) as modeled_air_quality_rows,
    cast(pipeline_version as varchar) as pipeline_version,
    coalesce(cast(pipeline_name as varchar), 'historical') as pipeline_name,
    coalesce(cast(status as varchar), 'SUCCESS') as status,
    coalesce(cast(requested_location_count as integer), 0) as requested_location_count,
    coalesce(cast(succeeded_location_count as integer), 0) as succeeded_location_count,
    coalesce(cast(failed_location_count as integer), 0) as failed_location_count,
    coalesce(cast(raw_objects_created as integer), 0) as raw_objects_created,
    coalesce(cast(raw_objects_reused as integer), 0) as raw_objects_reused,
    coalesce(cast(weather_forecast_rows as integer), 0) as weather_forecast_rows,
    coalesce(cast(air_quality_forecast_rows as integer), 0) as air_quality_forecast_rows,
    coalesce(cast(error_category as varchar), '') as error_category,
    coalesce(cast(error_summary as varchar), '') as error_summary
from {{ source('raw', 'pipeline_runs') }}
