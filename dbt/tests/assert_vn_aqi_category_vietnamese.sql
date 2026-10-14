-- The category names are QĐ 1459/QĐ-TCMT's own, spelled as the decision spells them,
-- and none of the Vietnamese guidance is stripped to ASCII. The dimension shipped as
-- "Kem", "Rat xau" and "Nguoi co trieu chung ..." until 2026-10-12, and the history page
-- showed those strings verbatim to readers. Any row returned here fails the build.
with expected as (
    select * from (
        values
            (1, 'Tốt'),
            (2, 'Trung bình'),
            (3, 'Kém'),
            (4, 'Xấu'),
            (5, 'Rất xấu'),
            (6, 'Nguy hại')
    ) as names(band_index, category_vi)
),

misnamed as (
    select
        coalesce(category.band_index, expected.band_index) as band_index,
        'category_vi ' || coalesce(category.category_vi, 'null') || ' should be '
        || coalesce(expected.category_vi, '(no band)') as problem
    from {{ ref('dim_vn_aqi_category') }} as category
    full outer join expected
        on category.band_index = expected.band_index
    where category.category_vi is distinct from expected.category_vi
),

stripped as (
    select
        band_index,
        'Vietnamese guidance written without diacritics' as problem
    from {{ ref('dim_vn_aqi_category') }}
    where regexp_matches(health_effect_vi, '^[ -~]*$')
       or regexp_matches(advice_general_vi, '^[ -~]*$')
       or regexp_matches(advice_sensitive_vi, '^[ -~]*$')
)

select * from misnamed
union all
select * from stripped
