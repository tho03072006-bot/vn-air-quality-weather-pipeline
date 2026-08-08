-- A serving anchor must resolve to exactly one air vintage and at most one
-- weather vintage. Before the vintage fix the mart picked its newest row per
-- pollutant, so a partially refreshed batch produced an anchor whose pollutants
-- spanned two model runs under a single issued-at label. count(distinct) skips
-- nulls, so an anchor with no weather at all is absent rather than mixed and
-- correctly passes.
select
    location_key,
    count(distinct forecast_issued_at_utc) as air_vintage_count,
    count(distinct weather_forecast_issued_at_utc) as weather_vintage_count
from {{ ref('mart_location_hourly_forecast') }}
group by 1
having count(distinct forecast_issued_at_utc) > 1
    or count(distinct weather_forecast_issued_at_utc) > 1
