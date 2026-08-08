"""Data provenance, coverage and method disclosures.

This is the page a reader opens when they are deciding whether to believe a number
they saw somewhere else in the app. It therefore states limitations plainly rather
than defensively, and it reads the live warehouse for the freshness and coverage
figures instead of describing them in prose that can drift out of date.
"""

import streamlit as st

from dashboard.components import methodology_expander, metric_row
from dashboard.runtime import cached_current, cached_pipeline_runs, require_warehouse
from dashboard.view_models import build_metric, format_age, freshness_view

st.title("Độ tin cậy dữ liệu")
st.caption("Nguồn, coverage, giới hạn và cách diễn giải các chỉ số trong sản phẩm.")

path = require_warehouse("dim_province", "mart_current_conditions")
current = cached_current(str(path))

if current.empty:
    st.warning("Chưa có dữ liệu để đánh giá độ tin cậy.", icon=":material/help:")
    st.stop()

# Measured from the warehouse, not asserted in prose. A page about trust that states
# its own coverage from memory is the first thing to go stale.
covered = int(current["province_code"].nunique())
aligned = current["is_vintage_aligned"]
mixed_vintage = int((~aligned.fillna(True).astype(bool)).sum())
oldest_fetch = current["forecast_age_minutes"].max()

metric_row(
    [
        build_metric("Tỉnh/thành có dự báo", covered, unit="/34", decimals=0),
        build_metric("Số điểm thiếu dự báo", 34 - covered, decimals=0),
        build_metric("Điểm lệch vintage", mixed_vintage, decimals=0),
        build_metric("Dữ liệu cũ nhất", oldest_fetch, unit="phút", decimals=0),
    ]
)

status_counts = current["freshness_status"].value_counts()
with st.container(horizontal=True, horizontal_alignment="left"):
    for status in ("FRESH", "DELAYED", "STALE"):
        count = int(status_counts.get(status, 0))
        if count == 0:
            continue
        text, icon, color = freshness_view(status, current["forecast_age_minutes"].median())
        st.badge(f"{status}: {count} điểm", icon=icon, color=color)

st.caption(
    "Ngưỡng freshness bám nhịp thu thập 6 giờ một lần: dưới 7 giờ là mới, tới 13 giờ là "
    "chậm, quá 13 giờ là cũ. Ngưỡng 3 giờ sẽ gắn cờ 'chậm' cho một nửa mỗi chu kỳ bình "
    f"thường. Dữ liệu cũ nhất hiện tại: {format_age(oldest_fetch)}."
)

if mixed_vintage:
    st.warning(
        f"{mixed_vintage} điểm đang lấy không khí và thời tiết từ hai lần chạy mô hình "
        "khác nhau. Các điểm này bị hạ độ tin cậy xuống LOW và có nhãn riêng trên trang "
        "Hôm nay.",
        icon=":material/call_split:",
    )

with st.container(border=True):
    st.subheader("Phân biệt nguồn")
    st.markdown(
        "- **Observed / quan trắc:** phép đo được OpenAQ tổng hợp từ station/sensor; "
        "coverage phụ thuộc nơi có trạm báo cáo.\n"
        "- **Modeled / mô hình:** ước tính CAMS qua Open-Meteo tại tọa độ đại diện; "
        "không phải phép đo tại mặt đất.\n"
        "- **MODELED_ONLY:** khu vực chưa có observation phù hợp không được giả lập trạm.\n"
        "- **Số đo bị nhà cung cấp gắn cờ** không tham gia vào giá trị công bố. Chúng "
        "không bị xóa mà giữ trong `mart_flagged_measurement_quarantine`, vì một chính "
        "sách loại trừ không xem lại được thì không phân biệt được với mất dữ liệu."
    )

with st.container(border=True):
    st.subheader("Những gì con số này không nói")
    # Stated as flat limitations rather than softened. A reader who over-trusts a
    # modelled anchor is the failure mode this page exists to prevent.
    st.markdown(
        "- **Một điểm không đại diện cho cả tỉnh.** Mỗi tỉnh/thành chỉ có một ô lưới mô "
        "hình. Bản đồ cố tình không tô kín diện tích tỉnh, vì tô kín sẽ khẳng định một "
        "độ phủ đồng đều mà dữ liệu không có.\n"
        "- **Điểm đại diện không phải trạm quan trắc**, và không được gọi là trạm ở bất "
        "kỳ đâu trong sản phẩm.\n"
        "- **`forecast_issued_at_utc` là thời điểm hệ thống lấy dữ liệu**, không phải "
        "thời điểm nhà cung cấp chạy mô hình. Nguồn không trả về thông tin đó.\n"
        "- **Điểm phù hợp ngoài trời là heuristic lập kế hoạch**, trừ điểm theo PM2.5, "
        "xác suất mưa, cảm giác nhiệt và UV. Không phải VN_AQI, không phải chỉ số y tế, "
        "và chưa cá nhân hóa.\n"
        "- **Khung giờ phù hợp là các giờ riêng lẻ được xếp hạng**, chưa phải một khoảng "
        "liên tục.\n"
        "- **Lịch sử chỉ có ba đô thị** (Hà Nội, TP.HCM, Đà Nẵng). Bản đồ và dự báo phủ "
        "34 tỉnh/thành nhưng chỉ bằng số liệu mô hình.\n"
        "- **Đà Nẵng không có trạm OpenAQ nào** trong vùng đã kiểm chứng, nên toàn bộ "
        "chuỗi của Đà Nẵng là số liệu mô hình.\n"
        "- **Tương quan không chứng minh nhân quả.** Sản phẩm này để tham khảo và lập kế "
        "hoạch, không dùng để chẩn đoán."
    )

with st.container(border=True):
    st.subheader("Độ tin cậy chưa phải độ chính xác")
    st.write(
        "Mức tin cậy hiện suy ra từ ba thứ: lead time, việc các trường bắt buộc có đủ "
        "hay không, và việc air/weather có cùng một lần chạy mô hình hay không. "
        "**Chưa có bất kỳ đối chiếu thực nghiệm nào với quan trắc**, nên sản phẩm không "
        "công bố con số sai số nào — không MAE, không RMSE, không bias."
    )
    st.caption(
        "Để nói được 'độ chính xác' cần một bảng verification ghép mỗi vintage với quan "
        "trắc đã xác nhận nó, rồi tính sai số theo địa điểm, chất và lead hour. Việc đó "
        "chưa được xây."
    )

with st.container(border=True):
    st.subheader("Bằng chứng pipeline đã chạy")
    runs = cached_pipeline_runs(str(path))
    if runs.empty:
        st.info("Chưa ghi nhận pipeline run nào.", icon=":material/history_toggle_off:")
    else:
        latest = runs.iloc[0]
        st.write(
            f"Run gần nhất: **{latest['pipeline_name']}** · kết quả **{latest['status']}** · "
            f"{int(latest['succeeded_location_count'])}/"
            f"{int(latest['requested_location_count'])} địa điểm."
        )
        partial = runs[runs["status"].isin(["PARTIAL", "FAILED"])]
        if not partial.empty:
            st.caption(
                f"{len(partial)}/{len(runs)} run gần đây chưa trọn vẹn. Chi tiết ở trang "
                "Pipeline health."
            )
        st.caption(
            "Mỗi lần chạy đều ghi một dòng audit kèm run_id, khoảng dữ liệu, thời lượng, "
            "số dòng theo nguồn và kết quả SUCCESS/PARTIAL/FAILED."
        )

with st.container(border=True):
    st.subheader("Cơ sở pháp lý của VN_AQI")
    st.write(
        "Chỉ số VN_AQI theo Quyết định 1459/QĐ-TCMT ngày 12/11/2019. Breakpoint và các "
        "mức được lưu dưới dạng dữ liệu (`dim_vn_aqi_breakpoint`, "
        "`dim_vn_aqi_category`) chứ không hardcode trong SQL, và một dbt test replay lại "
        "chính các ví dụ mẫu in trong mục 2.3 của quyết định — nên một breakpoint gõ sai "
        "sẽ làm build thất bại thay vì lặng lẽ lệch khỏi văn bản gốc."
    )

st.caption(
    "Trang này đọc trực tiếp warehouse, nên các con số ở trên phản ánh trạng thái hiện "
    "tại chứ không phải mô tả cố định."
)

methodology_expander()
