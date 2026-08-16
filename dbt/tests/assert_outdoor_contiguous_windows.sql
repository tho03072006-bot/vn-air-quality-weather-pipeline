-- This is intentionally a singular contract test rather than a duplicate of the
-- model implementation. The mart exposes each member hour, so the test can verify
-- adjacency and worst-hour scoring directly from the published row.
with violations as (
    select
        *,
        case
            when duration_hours = 2 then least(first_hour_score, second_hour_score)
            else least(first_hour_score, second_hour_score, third_hour_score)
        end as expected_worst_score
    from {{ ref('mart_outdoor_contiguous_window') }}
    where duration_hours not in (2, 3)
       or first_hour_utc is distinct from window_start_utc
       or second_hour_utc is distinct from window_start_utc + interval '1 hour'
       or (
           duration_hours = 2
           and (
               third_hour_utc is not null
               or third_hour_score is not null
               or window_end_utc is distinct from window_start_utc + interval '2 hours'
           )
       )
       or (
           duration_hours = 3
           and (
               third_hour_utc is distinct from window_start_utc + interval '2 hours'
               or third_hour_score is null
               or window_end_utc is distinct from window_start_utc + interval '3 hours'
           )
       )
       or abs(
           window_score
           - case
               when duration_hours = 2 then least(first_hour_score, second_hour_score)
               else least(first_hour_score, second_hour_score, third_hour_score)
           end
       ) > 0.0001
       or worst_hour_utc not in (first_hour_utc, second_hour_utc, third_hour_utc)
)

select * from violations
