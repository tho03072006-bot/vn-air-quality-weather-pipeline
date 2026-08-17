"""Data provenance, coverage and method disclosures.

This is the page a reader opens when they are deciding whether to believe a number
they saw somewhere else in the app. It therefore states limitations plainly rather
than defensively, and it reads the live warehouse for the freshness and coverage
figures instead of describing them in prose that can drift out of date.
"""

import pandas as pd
import streamlit as st

from dashboard.components import methodology_expander, metric_row, source_registry
from dashboard.runtime import (
    cached_current,
    cached_forecast_vs_analysis,
    cached_model_station_discrepancy,
    cached_pipeline_runs,
    cached_relation_exists,
    forecast_horizon_exhausted_message,
    require_warehouse,
)
from dashboard.view_models import build_metric, format_age, freshness_view

st.title("Độ tin cậy dữ liệu")
st.caption("Nguồn, coverage, giới hạn và cách diễn giải các chỉ số trong sản phẩm.")

path = require_warehouse("dim_province", "mart_current_conditions")
current = cached_current(str(path))

if current.empty:
    st.warning("Chưa có dữ liệu để đánh giá độ tin cậy.", icon=":material/help:")
    st.stop()

# This page does NOT stop on an exhausted horizon, unlike the pages that recommend
# something. Explaining whether a number can be trusted is exactly what it exists to
# do, and it is most needed when the answer is "not this one". The oldest anchor is
# quoted because it bounds the staleness of everything below.
horizon_flags = pd.Series(
    current.get("is_forecast_horizon_exhausted", False),
    index=current.index,
    dtype="boolean",
).fillna(False)
if bool(horizon_flags.any()):
    exhausted = current.loc[horizon_flags.astype(bool)].sort_values(
        "forecast_age_minutes",
        ascending=False,
        na_position="last",
    )
    st.warning(
        forecast_horizon_exhausted_message(exhausted.iloc[0]),
        icon=":material/history_toggle_off:",
    )

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

    st.markdown("**Nguồn dữ liệu và tình trạng giấy phép**")
    source_registry()

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
        "- **Khung giờ phù hợp là khoảng liên tục gồm 2 hoặc 3 giờ kề nhau, mỗi giờ "
        "đều đủ dữ liệu**; điểm của khoảng là điểm của giờ kém nhất, và một giờ thiếu "
        "dữ liệu sẽ ngắt khoảng. Giới hạn còn lại: khoảng chỉ dài 2–3 giờ và xếp hạng "
        "dựa trên heuristic `outdoor_score`, không phải VN_AQI hay chỉ số y tế.\n"
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
        "**Sản phẩm không công bố con số độ chính xác dự báo nào** — không MAE, không "
        "RMSE, không bias, dù bảng bên dưới đã đo được khoảng cách giữa mô hình và trạm."
    )
    st.caption(
        "Lý do khoảng cách đó chưa phải độ chính xác: một bên là ô lưới mô hình đại diện "
        "cả tỉnh, bên kia là một trạm đo ở một con phố. Khoảng cách giữa chúng gồm cả sai "
        "số mô hình lẫn sai lệch do hai thứ đo không cùng một đối tượng, và bảng này chưa "
        "tách được hai phần đó."
    )

# Published rather than withheld, and named for what it is. A gap this large is
# exactly what a reader deciding whether to trust a number needs to see, and hiding
# it while the rest of the page preaches transparency would be the worse failure.
#
# Existence is checked before loading, not assumed. The published warehouse asset is
# rebuilt on its own schedule, so a deployment can be running against a file built
# before this mart existed -- and this page in particular has to survive a warehouse
# that is missing something, since it is one of the pages a reader opens precisely
# when the data looks wrong.
if cached_relation_exists(str(path), "mart_model_station_discrepancy"):
    discrepancy = cached_model_station_discrepancy(str(path))
else:
    discrepancy = pd.DataFrame()

if not discrepancy.empty:
    with st.container(border=True):
        st.subheader("Chênh lệch giữa mô hình và trạm quan trắc")
        st.write(
            "Mỗi dòng ghép các giờ dự báo với quan trắc đã đo chính giờ đó. "
            "**Chỉ Hà Nội và TP.HCM có trạm**, nên bảng này không nói gì về 32 tỉnh/thành "
            "còn lại — những nơi đó không phải là chính xác hơn, mà là chưa từng được đo."
        )
        view = discrepancy.copy()
        view["Tỉ lệ mô hình/trạm"] = (
            view["mean_forecast_ugm3"] / view["mean_observed_ugm3"]
        ).round(2)
        st.dataframe(
            view[
                [
                    "location_key",
                    "pollutant",
                    "lead_band",
                    "paired_hours",
                    "mean_forecast_ugm3",
                    "mean_observed_ugm3",
                    "Tỉ lệ mô hình/trạm",
                    "mean_abs_discrepancy_ugm3",
                    "mean_signed_discrepancy_ugm3",
                ]
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "location_key": "Địa điểm",
                "pollutant": "Chất",
                "lead_band": "Lead time",
                "paired_hours": st.column_config.NumberColumn("Số giờ ghép được"),
                "mean_forecast_ugm3": st.column_config.NumberColumn("Mô hình TB", format="%.1f"),
                "mean_observed_ugm3": st.column_config.NumberColumn("Trạm TB", format="%.1f"),
                "mean_abs_discrepancy_ugm3": st.column_config.NumberColumn(
                    "Chênh lệch tuyệt đối TB", format="%.1f"
                ),
                "mean_signed_discrepancy_ugm3": st.column_config.NumberColumn(
                    "Lệch có dấu TB", format="%.1f"
                ),
            },
        )
        # The diagnostic that the two-column layout makes visible, and the reason the
        # table is worth a reader's attention rather than being a wall of numbers.
        st.caption(
            "Cách đọc bảng: **tỉ lệ giữ nguyên qua cả ba mức lead time** là dấu hiệu của "
            "lệch hệ thống, không phải sai số dự báo — vì kỹ năng dự báo thật thì phải "
            "kém đi khi dự xa hơn. Ngược lại, tỉ lệ quanh 1.0 mà chênh lệch tuyệt đối "
            "tăng dần theo lead time là sai số thời điểm, tức sai số dự báo thật."
        )
        st.caption(
            "Chỉ hiện con số khi có tối thiểu "
            f"{int(discrepancy['min_paired_hours'].max())} giờ ghép được; dưới ngưỡng đó "
            "ô sẽ trống thay vì hiện một con số dựng từ vài giờ lẻ."
        )

# The other half of the gap above, and the reason it can now be attributed. Rendered
# after the discrepancy table on purpose: a reader meets the 4.7x first and this
# explains it, rather than meeting an explanation for something they have not seen.
if cached_relation_exists(str(path), "mart_forecast_vs_analysis"):
    drift = cached_forecast_vs_analysis(str(path))
else:
    drift = pd.DataFrame()

if not drift.empty:
    with st.container(border=True):
        st.subheader("Khoảng cách đó có phải do dự báo sai không")
        st.write(
            "**Không.** Bảng dưới so dự báo với **phân tích của chính mô hình** cho "
            "cùng giờ, tại **cùng một toạ độ** — nên câu hỏi không gian bị loại bỏ "
            "hoàn toàn, và phần còn lại là phần duy nhất nói về việc dự báo. "
            "Dự báo tái tạo lại phân tích của mô hình; khoảng cách tới trạm nằm ở chỗ khác."
        )
        # The trap this table creates, stated before the numbers rather than after.
        st.warning(
            "**Điều này không có nghĩa dự báo chính xác.** Dự báo và phân tích là cùng "
            "một mô hình nói hai lần. Nếu mô hình đọc cao gấp 3,9 lần trạm tại điểm đó, "
            "thì một dự báo trung thành cũng cao gấp 3,9 lần. Bảng này **loại trừ** dự "
            "báo khỏi danh sách nguyên nhân, và không nói gì về việc con số nào đúng.",
            icon=":material/warning:",
        )
        drift_view = drift[
            [
                "location_key",
                "pollutant",
                "lead_band",
                "paired_hours",
                "mean_forecast_ugm3",
                "mean_analysis_ugm3",
                "mean_abs_drift_ugm3",
                "mean_signed_drift_ugm3",
            ]
        ]
        st.dataframe(
            drift_view,
            hide_index=True,
            width="stretch",
            column_config={
                "location_key": "Địa điểm",
                "pollutant": "Chất",
                "lead_band": "Lead time",
                "paired_hours": st.column_config.NumberColumn("Số giờ ghép được"),
                "mean_forecast_ugm3": st.column_config.NumberColumn("Dự báo TB", format="%.1f"),
                "mean_analysis_ugm3": st.column_config.NumberColumn("Phân tích TB", format="%.1f"),
                "mean_abs_drift_ugm3": st.column_config.NumberColumn(
                    "Lệch tuyệt đối TB", format="%.1f"
                ),
                "mean_signed_drift_ugm3": st.column_config.NumberColumn(
                    "Lệch có dấu TB", format="%.1f"
                ),
            },
        )
        st.caption(
            "Cách đọc: kỹ năng dự báo thật **phải kém đi khi dự xa hơn**. Nếu độ lệch ở "
            "đây phẳng qua cả ba mức lead time, và tỉ lệ mô hình–trạm ở bảng trên cũng "
            "phẳng y như vậy, thì cái phẳng đó là lệch hệ thống giữa hai phép đo khác "
            "nhau, không phải sai số dự báo."
        )
        st.caption(
            "Ô trống nghĩa là chưa đủ số giờ ghép được để nói gì, không phải bằng 0. "
            "Phân tích cũng đến sau như quan trắc: DAG daily nạp từng ngày UTC đã qua, "
            "nên dự báo cho ngày mai chưa có gì để đối chiếu."
        )
        st.caption(
            "Vẫn chưa tách được: trong khoảng cách mô hình–trạm còn lại, bao nhiêu là do "
            "ô lưới tỉnh không nằm đúng chỗ trạm, và bao nhiêu là do mô hình lệch ngay "
            "tại đúng vị trí đó. Việc đó cần lấy mô hình tại chính toạ độ trạm, và chưa "
            "được dựng."
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
