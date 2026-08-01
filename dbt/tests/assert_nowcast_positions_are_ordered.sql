-- Guards the positional contract the Nowcast depends on.
--
-- At the second hour of a series exactly two readings are in scope, so the
-- decision's formula reduces to (c1 + w*c2) / (1 + w) where c1 is the current
-- hour. If the list were ever built in the opposite order the result would be
-- (c2 + w*c1) / (1 + w) instead, which differs whenever the two hours differ.
-- Weight bounds are checked at the same time: w = max(Cmin/Cmax, 0.5) can only
-- land in [0.5, 1.0].
with ordered as (
    select
        city_key,
        pollutant,
        source_name,
        source_type,
        observed_at_utc,
        concentration,
        nowcast_weight,
        nowcast_concentration,
        recent_three_count,
        lag(concentration) over series as previous_concentration,
        row_number() over series as hour_position
    from {{ ref('int_city_pollutant_hourly') }}
    window series as (
        partition by city_key, pollutant, source_name, source_type
        order by observed_at_utc
    )
),

second_hour as (
    select
        *,
        (concentration + nowcast_weight * previous_concentration)
        / (1 + nowcast_weight) as expected_nowcast
    from ordered
    where hour_position = 2
      and concentration is not null
      and previous_concentration is not null
)

select
    city_key,
    pollutant,
    source_name,
    source_type,
    observed_at_utc,
    nowcast_concentration,
    expected_nowcast,
    nowcast_weight
from second_hour
where nowcast_concentration is null
   or abs(nowcast_concentration - expected_nowcast) > 1e-9

union all

select
    city_key,
    pollutant,
    source_name,
    source_type,
    observed_at_utc,
    nowcast_concentration,
    null,
    nowcast_weight
from ordered
where nowcast_weight is not null
  and (nowcast_weight < 0.5 or nowcast_weight > 1.0)
