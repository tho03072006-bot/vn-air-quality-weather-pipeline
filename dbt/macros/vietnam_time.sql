{% macro vietnam_aqi_business_date(timestamp_expression) -%}
    cast(
        timezone('Asia/Ho_Chi_Minh', {{ timestamp_expression }}) - interval '1 hour'
        as date
    )
{%- endmacro %}
