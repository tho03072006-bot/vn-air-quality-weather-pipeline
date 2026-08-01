-- Regression test against the worked examples in muc 2.3 of
-- Quyet dinh 1459/QD-TCMT. If a breakpoint or the interpolation formula is
-- edited by mistake, these six published results stop matching.
--
-- The decision also prints AQI_NO2 = 60 for a 1-hour NO2 mean of 118.7 ug/m3.
-- Cong thuc 1 gives (100-50)/(200-100) * (118.7-100) + 50 = 59.35, which rounds
-- to 59, so that one example is left out rather than encoding a rounding
-- inconsistency in the published document.
with reference as (
    select
        aqi_scale_key,
        cast(concentration as double) as concentration,
        cast(expected_aqi as integer) as expected_aqi,
        source_note
    from (
        values
            ('o3_1h', 136.1, 43, 'AQI gio, vi du b'),
            ('pm25', 20.3, 41, 'AQI gio Nowcast, vi du b'),
            ('o3_8h', 89.3, 45, 'AQI ngay, vi du c'),
            ('o3_1h', 114.6, 36, 'AQI ngay, vi du c'),
            ('no2', 130.8, 65, 'AQI ngay, vi du c'),
            ('pm25', 55.7, 110, 'AQI ngay, vi du c'),
            ('pm25', 0.0, 0, 'Lower bound of the scale'),
            ('pm25', 25.0, 50, 'Exact breakpoint boundary'),
            ('pm25', 5000.0, 500, 'Clamped above the top segment')
    ) as examples(aqi_scale_key, concentration, expected_aqi, source_note)
),

scored as (
    select
        reference.aqi_scale_key,
        reference.concentration,
        reference.expected_aqi,
        reference.source_note,
        {{ vn_aqi_value('reference.concentration') }} as actual_aqi
    from reference
    left join {{ ref('dim_vn_aqi_breakpoint') }} as bp
        on {{ vn_aqi_join_condition('reference.concentration', 'reference.aqi_scale_key') }}
)

select *
from scored
where actual_aqi is distinct from expected_aqi
