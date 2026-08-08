select
    province_key as location_key,
    province_code,
    province_name as location_name,
    anchor_name,
    'province_anchor' as location_type,
    latitude,
    longitude,
    timezone_name,
    true as is_default,
    true as is_precomputed,
    'WGS84' as coordinate_reference_system
from {{ ref('dim_province') }}
