-- Bang 2, Quyet dinh 1459/QD-TCMT (12/11/2019): BPi breakpoints in ug/m3.
-- Each row is one linear segment [bp_low, bp_high) mapped onto [aqi_low, aqi_high].
-- O3 has two scales: the 1-hour curve and the 8-hour curve, which stops at level 6
-- because the decision leaves levels 7 and 8 undefined for the 8-hour average.
select
    aqi_scale_key,
    pollutant,
    averaging_basis,
    level_index,
    cast(bp_low as double) as bp_low,
    cast(bp_high as double) as bp_high,
    cast(aqi_low as integer) as aqi_low,
    cast(aqi_high as integer) as aqi_high,
    is_top_level
from (
    values
        -- PM2.5
        ('pm25', 'pm25', '24h', 1, 0, 25, 0, 50, false),
        ('pm25', 'pm25', '24h', 2, 25, 50, 50, 100, false),
        ('pm25', 'pm25', '24h', 3, 50, 80, 100, 150, false),
        ('pm25', 'pm25', '24h', 4, 80, 150, 150, 200, false),
        ('pm25', 'pm25', '24h', 5, 150, 250, 200, 300, false),
        ('pm25', 'pm25', '24h', 6, 250, 350, 300, 400, false),
        ('pm25', 'pm25', '24h', 7, 350, 500, 400, 500, true),
        -- PM10
        ('pm10', 'pm10', '24h', 1, 0, 50, 0, 50, false),
        ('pm10', 'pm10', '24h', 2, 50, 150, 50, 100, false),
        ('pm10', 'pm10', '24h', 3, 150, 250, 100, 150, false),
        ('pm10', 'pm10', '24h', 4, 250, 350, 150, 200, false),
        ('pm10', 'pm10', '24h', 5, 350, 420, 200, 300, false),
        ('pm10', 'pm10', '24h', 6, 420, 500, 300, 400, false),
        ('pm10', 'pm10', '24h', 7, 500, 600, 400, 500, true),
        -- NO2 (1-hour basis for both the hourly and the daily index)
        ('no2', 'no2', '1h', 1, 0, 100, 0, 50, false),
        ('no2', 'no2', '1h', 2, 100, 200, 50, 100, false),
        ('no2', 'no2', '1h', 3, 200, 700, 100, 150, false),
        ('no2', 'no2', '1h', 4, 700, 1200, 150, 200, false),
        ('no2', 'no2', '1h', 5, 1200, 2350, 200, 300, false),
        ('no2', 'no2', '1h', 6, 2350, 3100, 300, 400, false),
        ('no2', 'no2', '1h', 7, 3100, 3850, 400, 500, true),
        -- O3 1-hour
        ('o3_1h', 'o3', '1h', 1, 0, 160, 0, 50, false),
        ('o3_1h', 'o3', '1h', 2, 160, 200, 50, 100, false),
        ('o3_1h', 'o3', '1h', 3, 200, 300, 100, 150, false),
        ('o3_1h', 'o3', '1h', 4, 300, 400, 150, 200, false),
        ('o3_1h', 'o3', '1h', 5, 400, 800, 200, 300, false),
        ('o3_1h', 'o3', '1h', 6, 800, 1000, 300, 400, false),
        ('o3_1h', 'o3', '1h', 7, 1000, 1200, 400, 500, true),
        -- O3 8-hour (undefined above 400 ug/m3, so level 5 is the top segment)
        ('o3_8h', 'o3', '8h', 1, 0, 100, 0, 50, false),
        ('o3_8h', 'o3', '8h', 2, 100, 120, 50, 100, false),
        ('o3_8h', 'o3', '8h', 3, 120, 170, 100, 150, false),
        ('o3_8h', 'o3', '8h', 4, 170, 210, 150, 200, false),
        -- Not flagged as a top level: above 400 ug/m3 the 8-hour curve is
        -- undefined, so the correct result is no sub-index rather than an
        -- extrapolation clamped to 500. mart_city_aqi_daily drops that branch
        -- explicitly and falls back to the 1-hour curve.
        ('o3_8h', 'o3', '8h', 5, 210, 400, 200, 300, false)
) as breakpoints(
    aqi_scale_key,
    pollutant,
    averaging_basis,
    level_index,
    bp_low,
    bp_high,
    aqi_low,
    aqi_high,
    is_top_level
)
