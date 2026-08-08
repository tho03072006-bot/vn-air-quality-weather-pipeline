select 'province_count' as failure
where (select count(*) from {{ ref('dim_province') }}) != 34

union all

select 'municipality_count'
where (
    select count(*) from {{ ref('dim_province') }} where unit_type = 'municipality'
) != 6

union all

select 'duplicate_code'
where (
    select count(distinct province_code) from {{ ref('dim_province') }}
) != 34
