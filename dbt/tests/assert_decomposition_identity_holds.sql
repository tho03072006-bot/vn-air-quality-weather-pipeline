-- The three terms must add up to the gap they claim to explain.
--
-- The whole argument of finding O is an identity:
--
--   anchor_model - observed = (anchor_model - station_model)   representativeness
--                           + (station_model - observed)       model offset
--
-- If the two published terms do not reconstruct the difference of the two published
-- means, then at least one of them was averaged over a different set of hours than
-- the other -- which is exactly what happens when a left join quietly drops rows, and
-- is invisible in any single column.
--
-- Compared on the rows where both terms exist, because the representativeness term is
-- deliberately measurable where no observation landed and the offset term is not.
-- A tolerance of 0.01 ugm3 covers double-precision accumulation over a few thousand
-- rows and nothing else; a real mismatch is orders of magnitude larger.
with published as (
    select
        station_id,
        pollutant,
        mean_anchor_model_ugm3,
        mean_observed_ugm3,
        mean_representativeness_ugm3,
        mean_model_offset_ugm3
    from {{ ref('mart_station_representativeness') }}
    where mean_representativeness_ugm3 is not null
      and mean_model_offset_ugm3 is not null
      and mean_anchor_model_ugm3 is not null
      and mean_observed_ugm3 is not null
)

select
    station_id,
    pollutant,
    mean_anchor_model_ugm3 - mean_observed_ugm3 as published_gap,
    mean_representativeness_ugm3 + mean_model_offset_ugm3 as sum_of_terms
from published
where abs(
    (mean_anchor_model_ugm3 - mean_observed_ugm3)
    - (mean_representativeness_ugm3 + mean_model_offset_ugm3)
) > 0.01
