-- Dense hourly spine per city/pollutant/source with the Nowcast weighted mean.
--
-- The spine matters: Nowcast weights depend on how many hours ago a reading
-- was taken, so a gap in the OpenAQ series must stay a gap rather than let the
-- previous reading slide into the wrong position.
--
-- Only micrograms per cubic metre enter this model. Bang 2 of the decision is
-- defined in that unit alone, so a ppb ozone sensor scored against it would
-- report roughly a five-hundredth of the true index. Rows in another unit are
-- dropped here rather than silently mis-scored downstream.
with measurements as (
    select
        city_key,
        pollutant,
        source_name,
        source_type,
        unit,
        observed_at_utc,
        concentration
    from {{ ref('mart_city_air_quality_hourly') }}
    where unit = 'µg/m³'
),

bounds as (
    select
        city_key,
        pollutant,
        source_name,
        source_type,
        min(observed_at_utc) as first_hour,
        max(observed_at_utc) as last_hour,
        datediff('hour', min(observed_at_utc), max(observed_at_utc)) as hour_count
    from measurements
    group by 1, 2, 3, 4
),

offset_bound as (
    select coalesce(max(hour_count), 0) as max_offset
    from bounds
),

offsets as (
    select unnest(generate_series(0, max_offset)) as hour_offset
    from offset_bound
),

spine as (
    select
        bounds.city_key,
        bounds.pollutant,
        bounds.source_name,
        bounds.source_type,
        bounds.first_hour + (offsets.hour_offset * interval 1 hour) as observed_at_utc
    from bounds
    inner join offsets
        on offsets.hour_offset <= bounds.hour_count
),

filled as (
    select
        spine.city_key,
        spine.pollutant,
        spine.source_name,
        spine.source_type,
        spine.observed_at_utc,
        -- Spine hours with no reading still carry the unit, because the model
        -- only admits one.
        coalesce(measurements.unit, 'µg/m³') as unit,
        measurements.concentration
    from spine
    left join measurements
        on spine.city_key = measurements.city_key
        and spine.pollutant = measurements.pollutant
        and spine.source_name = measurements.source_name
        and spine.source_type = measurements.source_type
        and spine.observed_at_utc = measurements.observed_at_utc
),

windowed as (
    select
        city_key,
        pollutant,
        source_name,
        source_type,
        observed_at_utc,
        unit,
        concentration,
        -- Element 1 is the current hour, element 12 is eleven hours earlier.
        -- Built from explicit lag() calls rather than a windowed array_agg:
        -- array_agg does not guarantee element order inside a window frame, and
        -- every Nowcast weight is positional, so a reordering would mis-weight
        -- the whole series with nothing to catch it. Hours with no reading, and
        -- positions before the start of the series, become NaN and later carry
        -- weight zero.
        list_value(
            {%- for lag_hours in range(0, 12) %}
            coalesce(
                {% if lag_hours == 0 -%}
                concentration
                {%- else -%}
                lag(concentration, {{ lag_hours }}) over trailing_hours
                {%- endif %},
                'nan'::double
            ){{ "," if not loop.last else "" }}
            {%- endfor %}
        ) as recent_values
    from filled
    window trailing_hours as (
        partition by city_key, pollutant, source_name, source_type
        order by observed_at_utc
    )
),

scored as (
    select
        *,
        len(list_filter(recent_values[1:3], x -> not isnan(x))) as recent_three_count,
        {{ vn_nowcast_weight('recent_values') }} as nowcast_weight
    from windowed
)

select
    city_key,
    pollutant,
    source_name,
    source_type,
    observed_at_utc,
    unit,
    concentration,
    recent_three_count,
    nowcast_weight,
    case
        when recent_three_count < 2 then null
        else {{ vn_nowcast_value('recent_values', 'nowcast_weight') }}
    end as nowcast_concentration
from scored
