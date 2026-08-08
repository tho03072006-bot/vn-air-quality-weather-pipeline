-- Grain: one forecast issue, location, valid hour and provider.
select * from {{ ref('stg_weather_forecast') }}
