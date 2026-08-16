"""Decision-first current conditions page."""

import pandas as pd
import streamlit as st

from dashboard.components import (
    methodology_expander,
    metric_row,
    source_badges,
)
from dashboard.components.charts import pm25_timeline, render
from dashboard.runtime import (
    cached_current,
    cached_forecast,
    cached_windows,
    forecast_horizon_exhausted_message,
    format_local_timestamp,
    primary_location,
    require_warehouse,
)
from dashboard.view_models import (
    POLLUTANT_LABELS,
    build_metric,
    dominant_pollutant,
    format_number,
)

st.title("Không khí hôm nay")
st.caption("Một câu trả lời nhanh về điều kiện hiện tại và thời điểm phù hợp hơn để ra ngoài.")

path = require_warehouse("dim_province", "mart_current_conditions", "mart_outdoor_decision_window")
location_key = primary_location(path)
current = cached_current(str(path), location_key)

if current.empty:
    st.warning(
        "Warehouse chưa có điều kiện hiện tại cho tỉnh/thành này. "
        "Vì chưa có snapshot phù hợp, trang không thể đưa ra khuyến nghị.",
        icon=":material/hourglass_empty:",
    )
    st.stop()

row = current.iloc[0]
# An exhausted horizon leaves a last-known row in place rather than an empty frame,
# so the page must check the flag. Rendering the recommendation from it would dress
# a four-day-old snapshot as current advice, which is the failure this page is most
# able to cause.
horizon_exhausted = row.get("is_forecast_horizon_exhausted", False)
if pd.notna(horizon_exhausted) and bool(horizon_exhausted):
    st.warning(
        forecast_horizon_exhausted_message(row),
        icon=":material/history_toggle_off:",
    )
    st.stop()

source_badges(row)

# Answer first. The recommendation and the one number behind it come before any
# chart, because the question the page exists to answer is "can I go outside now",
# and a reader who has to assemble that from four panels has not been answered.
DECISION_TONE = {
    "Phù hợp hơn": (":material/directions_run:", "green"),
    "Cân nhắc": (":material/error_outline:", "orange"),
    "Nên hạn chế": (":material/home:", "red"),
}
label = str(row["decision_label"])
icon, tone = DECISION_TONE.get(label, (":material/help:", "gray"))
with st.container(border=True):
    with st.container(horizontal=True, horizontal_alignment="left"):
        st.badge(label, icon=icon, color=tone)
        st.badge(
            f"Điểm {format_number(row.get('outdoor_score'), decimals=0)}/100",
            icon=":material/speed:",
            color="gray",
        )
    st.write(str(row["decision_explanation"]))
    st.caption(
        "Đánh giá dựa trên heuristic lập kế hoạch, không phải VN_AQI và không phải chỉ số y tế."
    )

# Built through build_metric so a null reading shows an em dash with an explanation
# instead of the string "nan µg/m³". The serving mart left-joins weather and its own
# confidence logic branches on these columns being null, so absence is expected.
metric_row(
    [
        build_metric("Điểm ngoài trời", row.get("outdoor_score"), unit="/100", decimals=0),
        build_metric("PM2.5 mô hình", row.get("pm25_ugm3"), unit="µg/m³"),
        build_metric("Cảm giác nhiệt", row.get("apparent_temperature_c"), unit="°C"),
        build_metric(
            "Khả năng mưa", row.get("precipitation_probability_pct"), unit="%", decimals=0
        ),
    ]
)

highest = dominant_pollutant(
    {
        pollutant: row.get(column)
        for pollutant, column in {
            "pm25": "pm25_ugm3",
            "pm10": "pm10_ugm3",
            "no2": "no2_ugm3",
            "o3": "o3_ugm3",
        }.items()
    }
)

with st.container(border=True):
    st.subheader("Điều cần biết")
    left, right = st.columns(2)
    # "Highest concentration", not "dominant": ranking raw µg/m³ across pollutants
    # says which number is biggest, while the VN_AQI sub-index says which matters.
    left.write(
        "**Chất ô nhiễm có nồng độ lớn nhất:** "
        f"{POLLUTANT_LABELS.get(highest, 'Chưa xác định') if highest else 'Chưa xác định'}"
    )
    left.write(f"**Thời điểm dự báo:** {format_local_timestamp(row['valid_at_local'])}")
    right.write(f"**Lần lấy forecast:** {format_local_timestamp(row['forecast_issued_at_utc'])}")
    right.write(f"**Số liệu tính lúc:** {format_local_timestamp(row['as_of_utc'])}")

with st.container(border=True):
    st.subheader("PM2.5 trong 24 giờ tới")
    st.caption(
        "Đường kẻ là ngưỡng 25 µg/m³, mức đầu tiên của Bảng 2 QĐ 1459/QĐ-TCMT. "
        "Đây là ngưỡng nồng độ, không phải giá trị VN_AQI."
    )
    timeline_source = cached_forecast(str(path), location_key, 24).copy()
    if timeline_source.empty:
        st.caption("Chưa có chuỗi dự báo theo giờ cho tỉnh/thành này.")
    else:
        timeline_source["valid_at_local"] = pd.to_datetime(timeline_source["valid_at_local"])
        render(
            pm25_timeline(timeline_source, hours=24),
            empty_message="Không có giá trị PM2.5 nào trong 24 giờ tới.",
        )

st.subheader("Các giờ phù hợp hơn trong 72 giờ tới")
st.caption("Xếp hạng từng giờ riêng lẻ, chưa phải một khoảng liên tục.")
windows = cached_windows(str(path), location_key)
if windows.empty:
    st.info("Chưa tìm thấy giờ nào đủ dữ liệu để xếp hạng.")
else:
    view = windows[
        [
            "valid_at_local",
            "outdoor_score",
            "decision_label",
            "pm25_ugm3",
            "precipitation_probability_pct",
            "apparent_temperature_c",
            "confidence_level",
            "decision_explanation",
        ]
    ].copy()
    view["valid_at_local"] = pd.to_datetime(view["valid_at_local"]).dt.strftime("%H:%M %d/%m")
    st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        column_config={
            "valid_at_local": "Giờ địa phương",
            "outdoor_score": st.column_config.ProgressColumn(
                "Điểm", min_value=0, max_value=100, format="%.0f"
            ),
            "decision_label": "Đánh giá",
            "pm25_ugm3": st.column_config.NumberColumn("PM2.5", format="%.1f µg/m³"),
            "precipitation_probability_pct": st.column_config.NumberColumn("Mưa", format="%.0f%%"),
            "apparent_temperature_c": st.column_config.NumberColumn("Cảm giác", format="%.1f °C"),
            "confidence_level": "Tin cậy",
            "decision_explanation": "Lý do",
        },
    )

methodology_expander()
