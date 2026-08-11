"""Preserve the observed-versus-modeled historical exploration workflow."""

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.components import methodology_expander, metric_row
from dashboard.data_access import load_filter_options, load_hourly_mart
from dashboard.runtime import POLLUTANT_LABELS, database_path
from dashboard.view_models import build_coverage, build_metric

CITY_LABELS = {
    "hanoi": "Hà Nội",
    "ho_chi_minh": "Hồ Chí Minh",
    "da_nang": "Đà Nẵng",
}


@st.cache_data(ttl="5m", max_entries=4)
def options(path: str) -> dict[str, object]:
    return load_filter_options(Path(path))


@st.cache_data(ttl="5m", max_entries=32)
def history(
    path: str,
    start_date: date,
    end_date: date,
    cities: tuple[str, ...],
    pollutant: str,
    source_type: str,
) -> pd.DataFrame:
    return load_hourly_mart(Path(path), start_date, end_date, cities, pollutant, source_type)


st.title("Lịch sử và xu hướng")
st.caption(
    "Dữ liệu lịch sử hiện giữ phạm vi ba đô thị pilot. Quan trắc OpenAQ và mô hình CAMS "
    "được lọc riêng, không trộn thành một chuỗi."
)

path = database_path()
if not path.exists():
    st.error("Không tìm thấy warehouse.")
    st.stop()

try:
    filter_options = options(str(path))
except Exception:
    st.warning("Các mart lịch sử chưa sẵn sàng. Hãy chạy pipeline và dbt build.")
    st.stop()

with st.form("history_filters"):
    date_range = st.date_input(
        "Khoảng ngày UTC",
        value=(filter_options["min_date"], filter_options["max_date"]),
        min_value=filter_options["min_date"],
        max_value=filter_options["max_date"],
    )
    cities = st.multiselect(
        "Đô thị",
        filter_options["cities"],
        default=filter_options["cities"],
        format_func=lambda value: CITY_LABELS.get(value, value),
    )
    # Default to PM2.5 rather than to pollutants[0]. The list arrives alphabetically,
    # so the first entry is NO2 -- for which the warehouse holds no observed rows at
    # all -- while the source control defaults to "observed". Every first visit to
    # this page therefore submitted a combination that could only return nothing, and
    # read as though the pipeline were broken.
    pollutants = list(filter_options["pollutants"])
    pollutant = st.selectbox(
        "Chất ô nhiễm",
        pollutants,
        index=pollutants.index("pm25") if "pm25" in pollutants else 0,
        format_func=lambda value: POLLUTANT_LABELS.get(value, value),
    )
    source_type = st.segmented_control(
        "Nguồn",
        filter_options["source_types"],
        default="observed" if "observed" in filter_options["source_types"] else None,
        format_func=lambda value: "Quan trắc OpenAQ" if value == "observed" else "Mô hình CAMS",
    )
    submitted = st.form_submit_button("Áp dụng", icon=":material/filter_alt:")

if not submitted:
    st.info("Chọn bộ lọc rồi nhấn **Áp dụng** để truy vấn lịch sử.")
    st.stop()
if not isinstance(date_range, tuple) or len(date_range) != 2 or not cities or not source_type:
    st.warning("Hãy chọn đủ khoảng ngày, địa điểm và nguồn.")
    st.stop()

data = history(str(path), date_range[0], date_range[1], tuple(cities), pollutant, source_type)
if data.empty:
    st.warning("Không có dữ liệu phù hợp. Điều này có thể phản ánh thiếu coverage quan trắc.")
    st.stop()

data["Tỉnh/thành"] = data["city_key"].map(CITY_LABELS)
data["observed_at_utc"] = pd.to_datetime(data["observed_at_utc"], utc=True)
# Both clocks, labelled. The filter is on UTC days, the reader thinks in local hours,
# and leaving only one of the two on the page makes an off-by-seven-hours reading
# almost inevitable.
data["observed_at_local"] = data["observed_at_utc"].dt.tz_convert("Asia/Ho_Chi_Minh")

metric_row(
    [
        build_metric("Số giờ dữ liệu", data["observed_at_utc"].nunique(), decimals=0),
        build_metric("Trung bình", data["concentration"].mean(), unit="µg/m³"),
        build_metric("Cao nhất", data["concentration"].max(), unit="µg/m³"),
        build_metric(
            "Số trạm/điểm",
            data["station_count"].max() if "station_count" in data.columns else None,
            decimals=0,
        ),
    ]
)
st.caption(
    "Nguồn: "
    + ("quan trắc OpenAQ" if source_type == "observed" else "mô hình CAMS")
    + ". Hai nguồn không bao giờ được trộn thành một chuỗi."
)

st.line_chart(
    data,
    x="observed_at_local",
    y="concentration",
    color="Tỉnh/thành",
    x_label="Giờ Việt Nam",
    y_label="Nồng độ (µg/m³)",
)

with st.container(border=True):
    st.subheader("Độ phủ theo ngày")
    st.caption(
        "Số giờ có dữ liệu trên 24 giờ mỗi ngày UTC, tính trên toàn bộ khoảng đã chọn: "
        "ngày hoàn toàn không có số đo được tính là 0 giờ chứ không bị bỏ khỏi biểu đồ. "
        "Với chuỗi quan trắc, thiếu giờ là bình thường và phản ánh coverage của trạm; "
        "với chuỗi mô hình, thiếu giờ là bất thường vì CAMS phủ đầy đủ về không gian."
    )
    # The selected cities, not the cities that happen to have rows. A city with no
    # rows anywhere in the range is absent from `data`, so deriving the axis from
    # `data` drops it from the strip instead of reporting it as empty -- the same
    # blindness as the missing-day case, one dimension over, and it fires on the
    # default filters: Da Nang has no observed PM2.5 at all.
    coverage = build_coverage(
        data,
        date_range[0],
        date_range[1],
        locations=[CITY_LABELS.get(city, city) for city in cities],
    )
    empty_days = int((coverage["hours_with_data"] == 0).sum())
    if empty_days:
        st.warning(
            f"{empty_days} ngày/địa điểm trong khoảng đã chọn không có số đo nào.",
            icon=":material/event_busy:",
        )
    st.bar_chart(
        coverage,
        x="data_date_utc",
        y="hours_with_data",
        color="Tỉnh/thành",
        x_label="Ngày UTC",
        y_label="Giờ có dữ liệu (tối đa 24)",
        stack=False,
    )
    incomplete = coverage[coverage["missing_hours"] > 0]
    if incomplete.empty:
        st.caption("Mọi ngày trong khoảng đã chọn đều đủ 24 giờ.")
    else:
        st.dataframe(
            incomplete.sort_values("missing_hours", ascending=False),
            hide_index=True,
            width="stretch",
            column_config={
                "Tỉnh/thành": "Tỉnh/thành",
                "data_date_utc": st.column_config.DateColumn("Ngày UTC", format="DD/MM/YYYY"),
                "hours_with_data": st.column_config.NumberColumn("Giờ có dữ liệu", format="%d"),
                "missing_hours": st.column_config.NumberColumn("Giờ thiếu", format="%d"),
            },
        )
st.download_button(
    "Tải dữ liệu CSV",
    data=data.to_csv(index=False).encode("utf-8-sig"),
    file_name="lich_su_chat_luong_khong_khi.csv",
    mime="text/csv",
    icon=":material/download:",
)

methodology_expander(
    extra=[
        (
            "Lịch sử chỉ có ba đô thị",
            "Chuỗi lịch sử giữ phạm vi Hà Nội, TP.HCM và Đà Nẵng. Bản đồ và dự báo "
            "phủ 34 tỉnh/thành nhưng chỉ bằng số liệu mô hình.",
        ),
        (
            "Quan trắc bị gắn cờ đã được loại",
            "Các số đo mà nhà cung cấp gắn cờ không tham gia vào giá trị công bố. "
            "Chúng vẫn tra được trong mart_flagged_measurement_quarantine.",
        ),
    ]
)
