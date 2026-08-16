"""Modeled 24–72 hour forecast page."""

import pandas as pd
import streamlit as st

from dashboard.components import methodology_expander, metric_row, source_badges
from dashboard.components.charts import (
    precipitation_panel,
    render,
    render_pollutant_panels,
    uv_panel,
    weather_panel,
)
from dashboard.runtime import (
    cached_current,
    cached_forecast,
    forecast_horizon_exhausted_message,
    format_local_timestamp,
    primary_location,
    require_warehouse,
)
from dashboard.view_models import build_metric

st.title("Dự báo 24–72 giờ")
st.caption("Xem chất lượng không khí, thời tiết và điểm phù hợp ngoài trời theo từng giờ.")

path = require_warehouse(
    "dim_province",
    "mart_current_conditions",
    "mart_location_hourly_forecast",
)
location_key = primary_location(path)

# The exhaustion flag lives on mart_current_conditions, and it has to be read before
# the horizon control renders. cached_forecast filters on the clock, so an exhausted
# anchor simply returns an empty frame here -- indistinguishable from a location that
# never had a forecast, which is precisely the ambiguity the flag exists to remove.
current = cached_current(str(path), location_key)
if current.empty:
    st.warning(
        "Warehouse chưa có snapshot hiện tại cho tỉnh/thành này. "
        "Không thể xác định vintage và phạm vi dự báo còn hiệu lực.",
        icon=":material/cloud_off:",
    )
    st.stop()

current_row = current.iloc[0]
horizon_exhausted = current_row.get("is_forecast_horizon_exhausted", False)
if pd.notna(horizon_exhausted) and bool(horizon_exhausted):
    st.warning(
        forecast_horizon_exhausted_message(current_row),
        icon=":material/history_toggle_off:",
    )
    st.stop()

horizon = st.segmented_control(
    "Khoảng dự báo", [24, 48, 72], default=72, format_func=lambda value: f"{value} giờ"
)

forecast = cached_forecast(str(path), location_key, int(horizon or 72))
if forecast.empty:
    st.warning(
        "Không có giờ dự báo nào còn khả dụng trong khoảng đã chọn. "
        "Trang không đưa ra khuyến nghị khi chuỗi dự báo trống.",
        icon=":material/cloud_off:",
    )
    st.stop()

head = forecast.iloc[0]
source_badges(head)

chart = forecast.copy()
chart["valid_at_local"] = pd.to_datetime(chart["valid_at_local"])

# Both vintages, stated separately. The serving mart no longer collapses them with
# max(), so the page must not either: when air and weather come from different model
# runs the reader needs to see that rather than be shown the newer of the two.
air_issued = format_local_timestamp(head.get("forecast_issued_at_utc"))
weather_issued = format_local_timestamp(head.get("weather_forecast_issued_at_utc"))
if str(air_issued) == str(weather_issued):
    st.caption(f"Lần chạy mô hình: {air_issued} · {len(forecast)} giờ khả dụng")
else:
    st.caption(
        f"Không khí lấy lúc {air_issued} · thời tiết lấy lúc {weather_issued} "
        f"— hai lần chạy khác nhau · {len(forecast)} giờ khả dụng"
    )

metric_row(
    [
        build_metric("Giờ có dữ liệu", len(forecast), unit="", decimals=0),
        build_metric("PM2.5 cao nhất", chart["pm25_ugm3"].max(), unit="µg/m³"),
        build_metric(
            "Điểm ngoài trời tốt nhất", chart["outdoor_score"].max(), unit="/100", decimals=0
        ),
        build_metric("Lead time xa nhất", chart["lead_hours"].max(), unit="giờ", decimals=0),
    ]
)

with st.container(border=True):
    st.subheader("Bụi và khí ô nhiễm")
    st.caption(
        "Mỗi chất một khung riêng với trục y độc lập. Đọc hình dạng theo thời gian "
        "trong từng khung; không so sánh độ cao giữa các khung vì thang khác nhau."
    )
    render_pollutant_panels(
        chart,
        empty_message="Không có chất ô nhiễm nào đủ dữ liệu để vẽ.",
    )

with st.container(border=True):
    st.subheader("Điểm phù hợp ngoài trời")
    st.caption("Heuristic lập kế hoạch, không phải VN_AQI.")
    st.area_chart(
        chart,
        x="valid_at_local",
        y="outdoor_score",
        x_label="Giờ Việt Nam",
        y_label="Điểm 0–100",
    )

left, right = st.columns(2)
with left:
    with st.container(border=True):
        st.subheader("Nhiệt độ")
        render(weather_panel(chart), empty_message="Không có dữ liệu nhiệt độ.")
with right:
    with st.container(border=True):
        st.subheader("Khả năng mưa")
        render(precipitation_panel(chart), empty_message="Không có dữ liệu mưa.")

with st.container(border=True):
    st.subheader("Chỉ số UV")
    st.caption("Tách riêng khỏi khả năng mưa: phần trăm và chỉ số 0–11 không cùng thang.")
    render(uv_panel(chart), empty_message="Không có dữ liệu UV.")

with st.expander("Xem dữ liệu theo giờ", icon=":material/table_view:"):
    st.dataframe(
        chart[
            [
                "valid_at_local",
                "lead_hours",
                "pm25_ugm3",
                "pm10_ugm3",
                "temperature_2m_c",
                "precipitation_probability_pct",
                "wind_speed_10m_kmh",
                "uv_index",
                "outdoor_score",
                "decision_label",
                "confidence_level",
            ]
        ],
        hide_index=True,
        width="stretch",
    )

methodology_expander()
