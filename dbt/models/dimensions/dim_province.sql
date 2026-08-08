select
    cast(province_code as varchar) as province_code,
    cast(province_key as varchar) as province_key,
    cast(province_name as varchar) as province_name,
    cast(unit_type as varchar) as unit_type,
    cast(anchor_name as varchar) as anchor_name,
    cast(latitude as double) as latitude,
    cast(longitude as double) as longitude,
    cast(timezone_name as varchar) as timezone_name
from {{ ref('provinces_2025') }}
