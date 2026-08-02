with coverage as (
    select *
    from {{ ref('mart_data_coverage') }}
),

latest_date as (
    select max(data_date_utc) as data_date_utc
    from coverage
)

select
    coverage.city_key,
    coverage.data_date_utc,
    coverage.pollutant,
    coverage.source_name,
    coverage.hours_with_data,
    coverage.expected_hours,
    coverage.missing_hours
from coverage
cross join latest_date
where coverage.data_date_utc < latest_date.data_date_utc
  -- OpenAQ observations are legitimately sparse; CAMS is the complete spatial baseline.
  and coverage.source_type = 'modeled'
  and coverage.hours_with_data < coverage.expected_hours
