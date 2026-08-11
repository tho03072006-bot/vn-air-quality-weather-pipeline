"""Nationwide province-anchor map."""

import pandas as pd
import pydeck as pdk
import streamlit as st

from dashboard.components import freshness_badge, methodology_expander, metric_row
from dashboard.map_legend import map_legend_html
from dashboard.runtime import cached_current, format_local_timestamp, require_warehouse
from dashboard.view_models import (
    MAP_METRICS,
    MISSING_DISPLAY,
    band_colour,
    band_for,
    build_metric,
    format_number,
    marker_radius,
    metric_by_key,
)

# One WebGL canvas on the page. Each PyDeck chart is its own context, and several
# on one page compete for GPU memory and make the whole app feel broken on modest
# hardware.
MAP_HEIGHT = 620

st.title("Bản đồ chất lượng không khí Việt Nam")
st.caption(
    "34 điểm đại diện cấp tỉnh/thành. Mỗi điểm là một ô lưới mô hình, "
    "không phải trạm quan trắc, và không nội suy thành độ chính xác cấp đường phố."
)

path = require_warehouse("dim_province", "mart_current_conditions")
current = cached_current(str(path))
if current.empty:
    st.warning(
        "Chưa có dữ liệu bản đồ toàn quốc. Hãy chạy "
        "`python -m vn_air_quality_weather.forecast_pipeline --all-provinces` rồi `dbt build`.",
        icon=":material/map:",
    )
    st.stop()

with st.container(horizontal=True, horizontal_alignment="left"):
    freshness_badge(current.iloc[0])

metric_key = st.segmented_control(
    "Chỉ số hiển thị",
    options=[metric.key for metric in MAP_METRICS],
    format_func=lambda key: metric_by_key(key).label,
    default=MAP_METRICS[0].key,
    key="map_metric",
)
metric = metric_by_key(metric_key or MAP_METRICS[0].key)

points = current.copy()
# Colour and radius both come from the same band lookup the legend renders, so the
# key and the map cannot describe different thresholds.
points["fill_color"] = points[metric.column].map(lambda value: list(band_colour(value, metric)))
points["marker_radius"] = points[metric.column].map(lambda value: marker_radius(value, metric))
points["metric_display"] = points[metric.column].map(
    lambda value: format_number(value, unit=metric.unit, decimals=metric.decimals)
)
points["band_label"] = points[metric.column].map(
    lambda value: band_for(value, metric).label if band_for(value, metric) else "Không có dữ liệu"
)

available = int(points[metric.column].notna().sum())
metric_row(
    [
        build_metric("Tỉnh/thành có dữ liệu", available, unit=f"/{len(points)}", decimals=0),
        build_metric(
            f"{metric.label} trung vị",
            points[metric.column].median(),
            unit=metric.unit,
            decimals=metric.decimals,
        ),
        build_metric(
            f"{metric.label} cao nhất",
            points[metric.column].max(),
            unit=metric.unit,
            decimals=metric.decimals,
        ),
        build_metric("Số điểm thiếu dữ liệu", len(points) - available, unit="", decimals=0),
    ]
)

# Legend is always visible, and every band carries its numeric range as text. A
# reader who cannot separate the hues still gets the thresholds.
st.caption(f"**Thang màu — {metric.label}.** {metric.legend_note}")
st.html(map_legend_html(metric))

layer = pdk.Layer(
    "ScatterplotLayer",
    id="province-anchors",
    data=points[
        [
            "location_key",
            "province_code",
            "province_name",
            "anchor_name",
            "latitude",
            "longitude",
            "fill_color",
            "marker_radius",
            "metric_display",
            "band_label",
            "decision_label",
            "confidence_level",
            "coverage_tier",
        ]
    ],
    get_position="[longitude, latitude]",
    get_fill_color="fill_color",
    get_radius="marker_radius",
    radius_min_pixels=6,
    radius_max_pixels=26,
    pickable=True,
    auto_highlight=True,
    stroked=True,
    get_line_color=[255, 255, 255],
    line_width_min_pixels=1,
)

deck = pdk.Deck(
    layers=[layer],
    initial_view_state=pdk.ViewState(latitude=16.0, longitude=106.5, zoom=4.4),
    # None so the basemap inherits the configured Streamlit theme instead of
    # forcing a light tileset into a dark page.
    map_style=None,
    tooltip={
        "html": (
            "<b>{province_name}</b><br/>"
            "Điểm đại diện: {anchor_name}<br/>"
            f"{metric.label}: "
            "{metric_display} ({band_label})<br/>"
            "Đánh giá: {decision_label}<br/>"
            "Tin cậy: {confidence_level} · {coverage_tier}"
        )
    },
)

selection = st.pydeck_chart(
    deck,
    width="stretch",
    height=MAP_HEIGHT,
    selection_mode="single-object",
    on_select="rerun",
    key="national_map",
)

selected_rows = []
if selection and getattr(selection, "selection", None):
    selected_rows = selection.selection.get("objects", {}).get("province-anchors", [])

if selected_rows:
    chosen = selected_rows[0]
    match = points[points["location_key"] == chosen.get("location_key")]
    if not match.empty:
        row = match.iloc[0]
        with st.container(border=True):
            st.subheader(f"{row['province_name']} — {row['anchor_name']}")
            metric_row(
                [
                    build_metric("PM2.5 mô hình", row.get("pm25_ugm3"), unit="µg/m³"),
                    build_metric("Cảm giác nhiệt", row.get("apparent_temperature_c"), unit="°C"),
                    build_metric(
                        "Khả năng mưa",
                        row.get("precipitation_probability_pct"),
                        unit="%",
                        decimals=0,
                    ),
                    build_metric(
                        "Điểm ngoài trời", row.get("outdoor_score"), unit="/100", decimals=0
                    ),
                ]
            )
            st.write(str(row.get("decision_explanation", "")))
            st.caption(
                f"Giờ dự báo: {format_local_timestamp(row.get('valid_at_local'))} · "
                f"Số liệu tính lúc: {format_local_timestamp(row.get('as_of_utc'))}"
            )
else:
    st.caption("Chọn một điểm trên bản đồ để xem chi tiết tỉnh/thành đó.")

# The table is not a fallback, it is the accessible equivalent: everything the map
# encodes as position and colour is here as sortable text.
st.subheader("Trạng thái theo tỉnh/thành")
TABLE_COLUMNS = [
    "province_code",
    "province_name",
    "anchor_name",
    "metric_display",
    "band_label",
    "pm25_ugm3",
    "temperature_2m_c",
    "outdoor_score",
    "decision_label",
    "confidence_level",
    "coverage_tier",
]
# Worst first for metrics where high is bad, best first otherwise, so the top of
# the table always answers "where should I not go" for the selected metric.
# Missing values sort last either way rather than masquerading as an extreme.
ascending = not metric.higher_is_worse
table = points.sort_values(metric.column, ascending=ascending, na_position="last")[TABLE_COLUMNS]
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "province_code": "Mã",
        "province_name": "Tỉnh/thành",
        "anchor_name": "Điểm đại diện",
        "metric_display": st.column_config.TextColumn(metric.label),
        "band_label": st.column_config.TextColumn("Mức"),
        "pm25_ugm3": st.column_config.NumberColumn("PM2.5 mô hình", format="%.1f µg/m³"),
        "temperature_2m_c": st.column_config.NumberColumn("Nhiệt độ", format="%.1f °C"),
        "outdoor_score": st.column_config.ProgressColumn(
            "Điểm ngoài trời", min_value=0, max_value=100, format="%.0f"
        ),
        "decision_label": "Đánh giá",
        "confidence_level": "Tin cậy",
        "coverage_tier": "Coverage",
    },
)

missing = points[points[metric.column].isna()]
if not missing.empty:
    st.caption(
        f"{len(missing)} tỉnh/thành không có giá trị {metric.label} cho giờ này: "
        + ", ".join(sorted(missing["province_name"].astype(str)))
        + f". Các ô này hiển thị màu xám và {MISSING_DISPLAY} trong bảng."
    )

csv = pd.DataFrame(table).to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "Tải CSV trạng thái toàn quốc",
    data=csv,
    file_name="trang_thai_khong_khi_34_tinh_thanh.csv",
    mime="text/csv",
    icon=":material/download:",
)

methodology_expander()
