-- Grain: one city at one UTC hour.
select * from {{ ref('stg_weather_hourly') }}
