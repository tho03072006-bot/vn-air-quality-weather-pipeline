"""Alert rule preview with production evaluation semantics."""

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from dashboard.runtime import (
    cached_current,
    choose_province,
    forecast_horizon_exhausted_message,
    require_warehouse,
)
from vn_air_quality_weather.alerts import AlertRule, AlertSnapshot, evaluate_alert

st.title("Cảnh báo cá nhân")
st.caption(
    "Cấu hình và kiểm tra quy tắc chống gửi trùng. Telegram chỉ gửi khi token/chat ID được "
    "cấu hình ở môi trường vận hành; trang này không hiển thị secret."
)

path = require_warehouse("dim_province", "mart_current_conditions")
location_key = choose_province(path, "alert_province")
current = cached_current(str(path), location_key)
if current.empty:
    st.warning("Không có điều kiện hiện tại để đánh giá cảnh báo.")
    st.stop()
row = current.iloc[0]

# Evaluating a rule against an expired snapshot would report a firing decision that
# corresponds to no real condition. The engine's own freshness check works on the
# snapshot it is handed, so it cannot see that the whole horizon has elapsed.
horizon_exhausted = row.get("is_forecast_horizon_exhausted", False)
if pd.notna(horizon_exhausted) and bool(horizon_exhausted):
    st.warning(
        forecast_horizon_exhausted_message(row),
        icon=":material/history_toggle_off:",
    )
    st.stop()

with st.form("alert_rule"):
    metric = st.selectbox(
        "Chỉ số",
        ["pm25", "pm10", "outdoor_score"],
        format_func=lambda value: {
            "pm25": "PM2.5 mô hình",
            "pm10": "PM10 mô hình",
            "outdoor_score": "Điểm ngoài trời",
        }[value],
    )
    direction = st.segmented_control(
        "Điều kiện",
        ["above", "below"],
        default="above",
        format_func=lambda x: "≥" if x == "above" else "≤",
    )
    threshold = st.number_input("Ngưỡng", min_value=0.0, value=35.0, step=1.0)
    quiet_start = st.time_input("Bắt đầu quiet hours", value=pd.Timestamp("22:00").time())
    quiet_end = st.time_input("Kết thúc quiet hours", value=pd.Timestamp("06:00").time())
    evaluate = st.form_submit_button("Kiểm tra quy tắc", icon=":material/notifications_active:")

if evaluate:
    column = {"pm25": "pm25_ugm3", "pm10": "pm10_ugm3", "outdoor_score": "outdoor_score"}[metric]
    issued = pd.Timestamp(row["forecast_issued_at_utc"])
    valid = pd.Timestamp(row["valid_at_utc"])
    if issued.tzinfo is None:
        issued = issued.tz_localize("UTC")
    if valid.tzinfo is None:
        valid = valid.tz_localize("UTC")
    decision = evaluate_alert(
        AlertRule(
            subscription_id="preview",
            location_key=location_key,
            metric=metric,
            threshold=threshold,
            direction=direction or "above",
            quiet_start=quiet_start,
            quiet_end=quiet_end,
        ),
        AlertSnapshot(
            valid_at_utc=valid.to_pydatetime(),
            fetched_at_utc=issued.to_pydatetime(),
            value=float(row[column]),
            source_type="modeled",
            coverage_tier=str(row["coverage_tier"]),
        ),
        now_utc=datetime.now(UTC),
    )
    if decision.should_send:
        st.success(f"Quy tắc sẽ tạo cảnh báo: {decision.reason}", icon=":material/check_circle:")
        st.caption(f"Idempotency key preview: `{decision.idempotency_key[:16]}…`")
    else:
        st.info(f"Chưa gửi: {decision.reason}", icon=":material/do_not_disturb_on:")

st.warning(
    "**Trang này chỉ mô phỏng, chưa gửi cảnh báo.** Engine đánh giá được freshness, "
    "quiet hours, threshold, cooldown và idempotency key, nhưng trong `src/"
    "vn_air_quality_weather/alerts.py` hiện **không có code gửi tin và không có lưu trữ**: "
    "không tin nào được gửi đi, không quy tắc nào được lưu lại sau khi bạn rời trang. "
    "`TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` đã có trong settings nhưng chưa được "
    "dùng để gửi, nên cấu hình chúng cũng chưa làm gì.",
    icon=":material/notifications_off:",
)
