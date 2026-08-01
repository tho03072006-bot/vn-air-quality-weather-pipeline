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
    coalesce(cast(raw_objects as integer), 0) as raw_objects,
    coalesce(cast(weather_rows as integer), 0) as weather_rows,
    coalesce(cast(observed_air_quality_rows as integer), 0) as observed_air_quality_rows,
    coalesce(cast(modeled_air_quality_rows as integer), 0) as modeled_air_quality_rows,
    cast(pipeline_version as varchar) as pipeline_version
from {{ source('raw', 'pipeline_runs') }}
