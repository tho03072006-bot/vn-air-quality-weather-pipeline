{#
    Helpers for Quyet dinh 1459/QD-TCMT (VN_AQI).

    vn_aqi_join_condition renders the range-join predicate against
    dim_vn_aqi_breakpoint. Segments are half-open [bp_low, bp_high) so a
    concentration matches exactly one row, except on the top segment which is
    unbounded above.

    vn_aqi_value renders Cong thuc 1:
        AQIx = (I(i+1) - I(i)) / (BP(i+1) - BP(i)) * (Cx - BP(i)) + I(i)
    rounded to an integer and clamped to the 0-500 publication scale.
#}

{% macro vn_aqi_join_condition(concentration, scale_key, breakpoint_alias='bp') -%}
    {{ concentration }} is not null
    and {{ concentration }} >= {{ breakpoint_alias }}.bp_low
    and (
        {{ concentration }} < {{ breakpoint_alias }}.bp_high
        or {{ breakpoint_alias }}.is_top_level
    )
    and {{ breakpoint_alias }}.aqi_scale_key = {{ scale_key }}
{%- endmacro %}


{#
    DuckDB follows Postgres and makes greatest()/least() ignore NULL arguments,
    so an unmatched breakpoint would silently clamp to 0. The explicit CASE
    keeps "no breakpoint" as NULL instead of a fake Good reading.
#}
{% macro vn_aqi_value(concentration, breakpoint_alias='bp') -%}
    case
        when {{ concentration }} is null
            or {{ breakpoint_alias }}.aqi_high is null
            or {{ breakpoint_alias }}.bp_high = {{ breakpoint_alias }}.bp_low
            then null
        else cast(
            least(
                greatest(
                    round(
                        ({{ breakpoint_alias }}.aqi_high - {{ breakpoint_alias }}.aqi_low)
                        / nullif(
                            {{ breakpoint_alias }}.bp_high - {{ breakpoint_alias }}.bp_low, 0
                        )
                        * ({{ concentration }} - {{ breakpoint_alias }}.bp_low)
                        + {{ breakpoint_alias }}.aqi_low
                    ),
                    0
                ),
                500
            ) as integer
        )
    end
{%- endmacro %}


{#
    Nowcast (muc 2.2.1a). The input is an ordered list of the trailing twelve
    1-hour means where element 1 is the current hour and element 12 is the
    oldest. Missing hours are encoded as NaN rather than NULL so that
    array_agg cannot silently drop them and shift every later position; a NaN
    contributes weight zero, matching "Neu ci khong co gia tri thi lay
    w(i-1) = 0".
#}
{% macro vn_nowcast_weight(recent_values) -%}
    greatest(
        coalesce(
            list_min(list_filter({{ recent_values }}, x -> not isnan(x)))
            / nullif(list_max(list_filter({{ recent_values }}, x -> not isnan(x))), 0),
            1.0
        ),
        0.5
    )
{%- endmacro %}


{% macro vn_nowcast_value(recent_values, weight) -%}
    list_sum(
        list_transform(
            {{ recent_values }},
            (c, i) -> case when isnan(c) then 0.0 else pow({{ weight }}, i - 1) * c end
        )
    )
    / nullif(
        list_sum(
            list_transform(
                {{ recent_values }},
                (c, i) -> case when isnan(c) then 0.0 else pow({{ weight }}, i - 1) end
            )
        ),
        0
    )
{%- endmacro %}
