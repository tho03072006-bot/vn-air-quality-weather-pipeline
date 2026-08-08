select location_key
from {{ ref('dim_location') }}
group by location_key
having count(*) != 1

union all

select 'registry_count'
where (select count(*) from {{ ref('dim_location') }}) != 34
