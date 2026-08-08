-- Grain: one forecast issue, location, valid hour, pollutant and provider.
select * from {{ ref('stg_air_quality_forecast') }}
