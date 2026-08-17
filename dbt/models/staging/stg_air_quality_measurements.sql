-- Unit normalisation matters twice over. It keeps one physical unit from
-- splitting the city/pollutant grain across spellings, and it lets the VN_AQI
-- models reject anything that is not micrograms per cubic metre, because the
-- Bang 2 breakpoints are only defined in that unit. OpenAQ v3 reports some
-- gaseous sensors in ppm or ppb, which would otherwise be scored against the
-- wrong scale and read as clean air.
with renamed as (
    select
        cast(city_key as varchar) as city_key,
        cast(station_id as varchar) as station_id,
        cast(station_name as varchar) as station_name,
        cast(sensor_id as bigint) as sensor_id,
        lower(cast(pollutant as varchar)) as pollutant,
        trim(lower(cast(unit as varchar))) as raw_unit,
        cast(observed_at_utc as timestamptz) as observed_at_utc,
        cast(value as double) as concentration,
        coalesce(cast(flagged as boolean), false) as flagged,
        -- Nullable, and not only because OpenAQ can omit them. Rows ingested before
        -- these columns existed carry nulls permanently, and backfilling them would
        -- mean rewriting immutable raw history to look like something it was not.
        -- Downstream reports the absence instead.
        try_cast(station_latitude as double) as station_latitude,
        try_cast(station_longitude as double) as station_longitude,
        cast(source_name as varchar) as source_name,
        cast(source_type as varchar) as source_type
    from {{ source('raw', 'air_quality_hourly') }}
)

select
    city_key,
    station_id,
    station_name,
    sensor_id,
    pollutant,
    case
        when raw_unit in ('µg/m³', 'ug/m3', 'ugm3', 'µg/m3', 'ug/m³', 'micrograms per cubic meter')
            then 'µg/m³'
        when raw_unit in ('mg/m³', 'mg/m3') then 'mg/m³'
        when raw_unit = 'ppm' then 'ppm'
        when raw_unit = 'ppb' then 'ppb'
        else coalesce(raw_unit, 'unknown')
    end as unit,
    observed_at_utc,
    concentration,
    flagged,
    station_latitude,
    station_longitude,
    source_name,
    source_type
from renamed
