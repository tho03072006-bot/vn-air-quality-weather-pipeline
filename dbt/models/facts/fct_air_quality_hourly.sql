-- Grain: one city/station/pollutant/source at one UTC hour.
select * from {{ ref('stg_air_quality_measurements') }}
