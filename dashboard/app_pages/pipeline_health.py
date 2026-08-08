"""Operational freshness and run audit page."""

import pandas as pd
import streamlit as st

from dashboard.components import metric_row
from dashboard.runtime import (
    cached_pipeline_health,
    cached_pipeline_runs,
    format_local_timestamp,
    require_warehouse,
)
from dashboard.view_models import build_metric

st.title("Pipeline health")
st.caption("Freshness, volume và run audit lấy trực tiếp từ warehouse.")

path = require_warehouse("fct_pipeline_run")
health = cached_pipeline_health(str(path))
runs = cached_pipeline_runs(str(path))

if health.empty:
    st.warning("Chưa có health metrics.")
else:
    now = pd.Timestamp.now(tz="UTC")
    health["latest_fetch_utc"] = pd.to_datetime(health["latest_fetch_utc"], utc=True)
    health["age_hours"] = (now - health["latest_fetch_utc"]).dt.total_seconds() / 3600
    health["status"] = health["age_hours"].map(lambda age: "OK" if age <= 36 else "STALE")
    metrics = st.columns(4)
    metrics[0].metric("Nguồn được giám sát", len(health))
    metrics[1].metric("Nguồn OK", int((health["status"] == "OK").sum()))
    metrics[2].metric("Tổng raw rows", f"{int(health['row_count'].sum()):,}")
    metrics[3].metric("Nguồn stale", int((health["status"] == "STALE").sum()))
    st.dataframe(
        health,
        hide_index=True,
        column_config={
            "source": "Nguồn",
            "row_count": st.column_config.NumberColumn("Rows", format="%d"),
            "latest_fetch_utc": st.column_config.DatetimeColumn(
                "Fetch mới nhất", format="DD/MM/YYYY HH:mm"
            ),
            "age_hours": st.column_config.NumberColumn("Tuổi dữ liệu (giờ)", format="%.1f"),
            "status": "Trạng thái",
        },
    )

st.subheader("Các pipeline run gần nhất")
if runs.empty:
    st.info("Chưa ghi nhận pipeline run.")
else:
    # A run is not pass/fail. PARTIAL means some locations landed and some did not,
    # and it is the outcome most worth surfacing: nothing errored, so nobody gets
    # paged, yet the warehouse is incomplete.
    status_counts = runs["status"].value_counts()
    metric_row(
        [
            build_metric("Run được ghi nhận", len(runs), decimals=0),
            build_metric("SUCCESS", int(status_counts.get("SUCCESS", 0)), decimals=0),
            build_metric("PARTIAL", int(status_counts.get("PARTIAL", 0)), decimals=0),
            build_metric("FAILED", int(status_counts.get("FAILED", 0)), decimals=0),
        ]
    )

    latest = runs.iloc[0]
    st.caption(
        f"Run mới nhất: {latest['pipeline_name']} · {latest['status']} · hoàn tất "
        f"{format_local_timestamp(latest['finished_at_utc'])}"
    )

    successes = runs[runs["status"] == "SUCCESS"]
    problems = runs[runs["status"].isin(["PARTIAL", "FAILED"])]
    left, right = st.columns(2)
    with left:
        if successes.empty:
            st.warning("Chưa có run nào thành công hoàn toàn.", icon=":material/error:")
        else:
            row = successes.iloc[0]
            st.success(
                f"Lần thành công gần nhất: {row['pipeline_name']} lúc "
                f"{format_local_timestamp(row['finished_at_utc'])}",
                icon=":material/check_circle:",
            )
    with right:
        if problems.empty:
            st.info("Không có run nào thất bại hoặc dở dang.", icon=":material/done_all:")
        else:
            row = problems.iloc[0]
            # error_summary is written by the pipeline with query strings stripped, so
            # it is safe to render; never substitute a raw exception here.
            detail = str(row.get("error_summary") or "").strip()
            st.warning(
                f"Gần nhất chưa trọn vẹn: {row['pipeline_name']} · {row['status']} · "
                f"{int(row['succeeded_location_count'])}/"
                f"{int(row['requested_location_count'])} địa điểm"
                + (f" — {detail}" if detail else ""),
                icon=":material/warning:",
            )

    st.dataframe(
        runs,
        hide_index=True,
        width="stretch",
        column_config={
            "run_id": "Run ID",
            "pipeline_name": "Pipeline",
            "status": "Kết quả",
            "data_date_utc": st.column_config.DateColumn("Ngày dữ liệu (UTC)", format="DD/MM/YYYY"),
            "finished_at_utc": st.column_config.DatetimeColumn(
                "Hoàn tất", format="DD/MM/YYYY HH:mm"
            ),
            "duration_seconds": st.column_config.NumberColumn("Thời lượng (s)", format="%.0f"),
            "raw_objects_attempted": st.column_config.NumberColumn("Raw thử ghi", format="%d"),
            "raw_objects_created": st.column_config.NumberColumn("Raw tạo mới", format="%d"),
            "raw_objects_reused": st.column_config.NumberColumn("Raw dùng lại", format="%d"),
            "requested_location_count": st.column_config.NumberColumn("Yêu cầu", format="%d"),
            "succeeded_location_count": st.column_config.NumberColumn("Thành công", format="%d"),
            "failed_location_count": st.column_config.NumberColumn("Thất bại", format="%d"),
            "total_rows": st.column_config.NumberColumn("Rows lịch sử", format="%d"),
            "total_forecast_rows": st.column_config.NumberColumn("Rows forecast", format="%d"),
            "error_category": "Loại lỗi",
            "error_summary": "Chi tiết lỗi",
            "is_latest_run_for_date": "Mới nhất cho ngày",
        },
    )
    st.caption(
        "Raw tạo mới và dùng lại tách riêng. Khóa object raw có chứa run_id, nên chạy "
        "lại thủ công (run_id mới) luôn ghi object mới và báo 'tạo mới'. 'Dùng lại' "
        "xuất hiện khi cùng một run_id ghi lại cùng nội dung — trong thực tế là task "
        "Airflow retry, vì ở đó run_id do Airflow cấp và giữ nguyên qua các lần thử."
    )

st.caption(
    "dbt source freshness là gate riêng trong `scripts/verify.ps1`; API rate-limit và DAG "
    "runtime status sẽ được ghi thêm khi chạy Airflow production thay vì demo fixture."
)
