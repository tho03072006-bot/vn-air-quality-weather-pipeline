with cases as (
    select *
    from (
        values
            (timestamptz '2026-07-26 17:59:59+00', date '2026-07-26'),
            (timestamptz '2026-07-26 18:00:00+00', date '2026-07-27'),
            (timestamptz '2026-07-27 16:59:59+00', date '2026-07-27'),
            (timestamptz '2026-07-27 17:00:00+00', date '2026-07-27')
    ) as expected(observed_at_utc, expected_business_date)
)

select *
from cases
where {{ vietnam_aqi_business_date('observed_at_utc') }} != expected_business_date
