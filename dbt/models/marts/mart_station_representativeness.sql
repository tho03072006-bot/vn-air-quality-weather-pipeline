-- How much of the model-station gap is distance rather than the model being wrong.
--
-- This is the second half of finding O, and the half that needs no observation at
-- all. It compares the model with itself at two points -- the province anchor the
-- product serves, and the coordinates of the station the product is judged against --
-- so it measures the consequence of the two not being the same place.
--
--   published gap   = forecast drift          (mart_forecast_vs_analysis, ~1.0)
--                   + representativeness      (this model)
--                   + model offset at station (the remainder, published here too)
--
-- Because it needs no station reading, the representativeness column is measurable
-- wherever the model has been sampled at a station's position, including hours the
-- station itself did not report.
--
-- The offset column is different and is gated separately: it needs the station's own
-- measurement, so it exists only where an observation landed for that hour.
--
-- **None of these three columns is forecast accuracy, and their sum is not either.**
-- Distance between two model points is not error. A model offset from one instrument
-- at one street is not the model being wrong across a province. What the split buys
-- is the ability to stop attributing all of it to the forecast, which was never true
-- and is now measured rather than argued.
--
-- A view, matching every other relation in this family.
{{ config(materialized='view') }}

{% set min_paired_hours = 30 %}

with station_model as (
    select
        station_id,
        city_key,
        pollutant,
        observed_at_utc,
        concentration as station_model_ugm3,
        station_latitude,
        station_longitude
    from {{ ref('stg_air_quality_at_station') }}
),

anchor_model as (
    select
        city_key,
        pollutant,
        observed_at_utc,
        concentration as anchor_model_ugm3
    from {{ ref('mart_city_air_quality_hourly') }}
    where source_type = 'modeled'
),

station_observed as (
    select
        city_key,
        pollutant,
        observed_at_utc,
        concentration as observed_ugm3
    from {{ ref('mart_city_air_quality_hourly') }}
    where source_type = 'observed'
),

paired as (
    select
        station_model.station_id,
        station_model.city_key,
        station_model.pollutant,
        station_model.observed_at_utc,
        station_model.station_latitude,
        station_model.station_longitude,
        anchor_model.anchor_model_ugm3,
        station_model.station_model_ugm3,
        station_observed.observed_ugm3
    from station_model
    inner join anchor_model
        on station_model.city_key = anchor_model.city_key
        and station_model.pollutant = anchor_model.pollutant
        and station_model.observed_at_utc = anchor_model.observed_at_utc
    left join station_observed
        on station_model.city_key = station_observed.city_key
        and station_model.pollutant = station_observed.pollutant
        and station_model.observed_at_utc = station_observed.observed_at_utc
),

aggregated as (
    select
        station_id,
        city_key,
        pollutant,
        max(station_latitude) as station_latitude,
        max(station_longitude) as station_longitude,
        count(*) as model_paired_hours,
        count_if(observed_ugm3 is not null) as observed_paired_hours,
        min(observed_at_utc) as first_valid_at_utc,
        max(observed_at_utc) as last_valid_at_utc,
        -- Over every hour the model was sampled at both points. This is the whole
        -- reason the representativeness term is worth having separately: it needs no
        -- observation, so it is measurable on hours the station never reported.
        avg(anchor_model_ugm3 - station_model_ugm3) as mean_representativeness_all_raw,
        avg(abs(anchor_model_ugm3 - station_model_ugm3)) as mean_abs_representativeness_all_raw,
        -- Everything below is restricted to hours that carry all three values.
        --
        -- The first version of this model averaged each term over whatever hours it
        -- had, and assert_decomposition_identity_holds failed on four series. It was
        -- right to: the decomposition is an identity per hour, and two means taken
        -- over different sets of hours do not reconstruct the difference of two other
        -- means taken over different sets again. Sharing one sample is what makes the
        -- published split add up to the published gap.
        avg(anchor_model_ugm3) filter (where observed_ugm3 is not null)
            as mean_anchor_model_raw,
        avg(station_model_ugm3) filter (where observed_ugm3 is not null)
            as mean_station_model_raw,
        avg(observed_ugm3) as mean_observed_raw,
        avg(anchor_model_ugm3 - station_model_ugm3) filter (where observed_ugm3 is not null)
            as mean_representativeness_raw,
        avg(station_model_ugm3 - observed_ugm3) as mean_offset_raw
    from paired
    group by station_id, city_key, pollutant
)

select
    station_id,
    city_key,
    pollutant,
    station_latitude,
    station_longitude,
    model_paired_hours,
    observed_paired_hours,
    first_valid_at_utc,
    last_valid_at_utc,
    {{ min_paired_hours }} as min_paired_hours,
    model_paired_hours >= {{ min_paired_hours }} as has_sufficient_model_sample,
    observed_paired_hours >= {{ min_paired_hours }} as has_sufficient_observed_sample,
    -- Measured on every sampled hour, including hours with no station reading. Named
    -- for that scope so it cannot be mistaken for a term of the published split.
    case when model_paired_hours >= {{ min_paired_hours }} then mean_representativeness_all_raw end
        as mean_representativeness_all_hours_ugm3,
    case when model_paired_hours >= {{ min_paired_hours }} then mean_abs_representativeness_all_raw end
        as mean_abs_representativeness_all_hours_ugm3,
    -- The four columns below share one sample, the hours carrying all three values,
    -- so representativeness + model offset reconstructs anchor - observed exactly.
    -- assert_decomposition_identity_holds fails if they ever stop doing so.
    case when observed_paired_hours >= {{ min_paired_hours }} then mean_representativeness_raw end
        as mean_representativeness_ugm3,
    case when observed_paired_hours >= {{ min_paired_hours }} then mean_offset_raw end
        as mean_model_offset_ugm3,
    case when observed_paired_hours >= {{ min_paired_hours }} then mean_anchor_model_raw end
        as mean_anchor_model_ugm3,
    case when observed_paired_hours >= {{ min_paired_hours }} then mean_station_model_raw end
        as mean_station_model_ugm3,
    case when observed_paired_hours >= {{ min_paired_hours }} then mean_observed_raw end
        as mean_observed_ugm3
from aggregated
