-- The audit row is the only evidence that a run happened, so its arithmetic has
-- to hold or the Pipeline health page reports a run that never occurred as
-- described. Three invariants:
--   1. created + reused accounts for every attempted raw object. Before the
--      counts were split, every write was reported as created, so a replay that
--      reused all of its objects looked like fresh ingestion.
--   2. succeeded + failed accounts for every requested location.
--   3. the status agrees with the counts, so a PARTIAL run cannot be filed as
--      SUCCESS and disappear from an operator's attention.
select
    run_id,
    pipeline_name,
    status,
    raw_objects_attempted,
    raw_objects_created,
    raw_objects_reused,
    requested_location_count,
    succeeded_location_count,
    failed_location_count
from {{ ref('fct_pipeline_run') }}
where raw_objects_created + raw_objects_reused != raw_objects_attempted
   or (
       requested_location_count > 0
       and succeeded_location_count + failed_location_count != requested_location_count
   )
   or (status = 'SUCCESS' and failed_location_count > 0)
   or (status = 'PARTIAL' and (failed_location_count = 0 or succeeded_location_count = 0))
   or (status = 'FAILED' and succeeded_location_count > 0)
