"""Compare up to five province anchors."""

import pandas as pd
import streamlit as st

from dashboard.components import methodology_expander
from dashboard.runtime import (
    cached_current,
    forecast_horizon_exhausted_message,
    province_options,
    require_warehouse,
)
from dashboard.view_models import (
    ACTIVITY_PRIORITIES,
    MISSING_DISPLAY,
    activity_by_key,
    format_number,
)

# Each panel gets its own axis and its own unit. Putting µg/m³, °C, % and a 0-100
# score on one chart would let a reader compare bar heights that mean nothing to
# each other.
COMPARISON_PANELS = (
    ("pm25_ugm3", "PM2.5 mô hình", "µg/m³", 1),
    ("outdoor_score", "Điểm phù hợp ngoài trời", "/100", 0),
    ("apparent_temperature_c", "Cảm giác nhiệt", "°C", 1),
    ("precipitation_probability_pct", "Khả năng mưa", "%", 0),
)

st.title("So sánh địa điểm")
st.caption("So sánh 3–5 tỉnh/thành trên cùng thời điểm hiện tại.")

path = require_warehouse("dim_province", "mart_current_conditions")
keys, labels = province_options(path)
selected = st.multiselect(
    "Chọn tối đa 5 tỉnh/thành",
    keys,
    default=["hanoi", "ho_chi_minh", "da_nang"],
    max_selections=5,
    format_func=lambda value: labels.get(value, value),
)
if not selected:
    st.info("Chọn ít nhất một tỉnh/thành để so sánh.", icon=":material/checklist:")
    st.stop()

activity_key = st.segmented_control(
    "Xếp hạng cho mục đích",
    options=[activity.key for activity in ACTIVITY_PRIORITIES],
    format_func=lambda key: activity_by_key(key).label,
    default=ACTIVITY_PRIORITIES[0].key,
    key="compare_activity",
)
activity = activity_by_key(activity_key or ACTIVITY_PRIORITIES[0].key)
# Stated in full, because a ranking whose rule is invisible is just an assertion.
st.caption(f"**{activity.label}.** {activity.explanation}")

current = cached_current(str(path))
comparison = current[current["location_key"].isin(selected)].copy()
if comparison.empty:
    st.warning("Chưa có dữ liệu hiện tại cho các tỉnh/thành đã chọn.", icon=":material/search_off:")
    st.stop()

# Built through pd.Series with an explicit default rather than indexing the column,
# so a warehouse predating the flag degrades to "not exhausted" instead of raising a
# KeyError and taking the page down.
horizon_flags = pd.Series(
    comparison.get("is_forecast_horizon_exhausted", False),
    index=comparison.index,
    dtype="boolean",
).fillna(False)
if bool(horizon_flags.any()):
    # Any expired anchor stops the ranking. A table that silently mixes current and
    # expired rows would rank them against each other as though they were comparable.
    expired_row = comparison.loc[horizon_flags.astype(bool)].iloc[0]
    st.warning(
        forecast_horizon_exhausted_message(expired_row),
        icon=":material/history_toggle_off:",
    )
    st.stop()

sort_columns = [column for column, _ in activity.sort_columns if column in comparison.columns]
ascending = [asc for column, asc in activity.sort_columns if column in comparison.columns]
if sort_columns:
    # Missing values sort last under either direction, so a location with no reading
    # never wins the ranking by default.
    comparison = comparison.sort_values(sort_columns, ascending=ascending, na_position="last")

st.subheader("Bảng xếp hạng")
ranking = comparison.copy()
ranking.insert(0, "Hạng", range(1, len(ranking) + 1))
for column, label, unit, decimals in COMPARISON_PANELS:
    if column in ranking.columns:
        ranking[label] = ranking[column].map(
            lambda value, unit=unit, decimals=decimals: format_number(
                value, unit=unit, decimals=decimals
            )
        )
st.dataframe(
    ranking[
        [
            "Hạng",
            "province_name",
            *[label for column, label, _, _ in COMPARISON_PANELS if column in ranking.columns],
            "decision_label",
            "confidence_level",
            "coverage_tier",
        ]
    ],
    hide_index=True,
    width="stretch",
    column_config={
        "province_name": "Tỉnh/thành",
        "decision_label": "Đánh giá",
        "confidence_level": "Tin cậy",
        "coverage_tier": "Coverage",
    },
)

st.subheader("So sánh từng chỉ số")
st.caption(
    "Mỗi chỉ số một khung với trục riêng. Không so sánh chiều dài cột giữa các khung "
    "vì đơn vị khác nhau."
)
columns = st.columns(2)
for index, (column, label, unit, _decimals) in enumerate(COMPARISON_PANELS):
    if column not in comparison.columns:
        continue
    panel = comparison[["province_name", column]].dropna(subset=[column])
    with columns[index % 2]:
        with st.container(border=True):
            st.markdown(f"**{label}** ({unit})")
            if panel.empty:
                st.caption(f"Không có dữ liệu {label} cho các tỉnh/thành đã chọn.")
            else:
                st.bar_chart(
                    panel,
                    x="province_name",
                    y=column,
                    x_label="Tỉnh/thành",
                    y_label=unit,
                    horizontal=True,
                )

absent = [
    label
    for column, label, _, _ in COMPARISON_PANELS
    if column in comparison.columns and comparison[column].isna().all()
]
if absent:
    st.caption(
        "Không có dữ liệu cho: "
        + ", ".join(absent)
        + f". Các ô này hiển thị {MISSING_DISPLAY} trong bảng."
    )

methodology_expander()
