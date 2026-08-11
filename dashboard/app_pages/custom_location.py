"""On-demand forecast for a searched place or custom Vietnam coordinate."""

import httpx
import pandas as pd
import streamlit as st

from dashboard.components import methodology_expander, metric_row
from dashboard.components.charts import render_pollutant_panels
from dashboard.location_search import match_provinces, merge_search_results
from dashboard.runtime import (
    cached_location_search,
    cached_on_demand_forecast,
    cached_provinces,
    database_path,
    format_local_timestamp,
    source_badges,
)
from dashboard.view_models import build_metric
from vn_air_quality_weather.geography import nearest_province, validate_vietnam_coordinates

st.title("Địa điểm tùy chọn")
st.caption(
    "Tìm địa danh hoặc nhập tọa độ WGS84 để lấy dự báo mô hình theo yêu cầu. "
    "Kết quả được cache 15 phút và không ghi vào warehouse."
)

st.session_state.setdefault("custom_location", None)
st.session_state.setdefault("custom_search_results", ())
st.session_state.setdefault("custom_search_query", "")
st.session_state.setdefault("custom_search_geocoding_unavailable", False)
st.session_state.setdefault("custom_search_input", st.session_state.custom_search_query)
if st.session_state.custom_search_results and not st.session_state.custom_search_query:
    # Discard pre-fix results that have no stored query to identify what they match.
    st.session_state.custom_search_results = ()


def clear_custom_search() -> None:
    """Clear submitted and widget state before a location-mode rerun."""

    st.session_state.custom_search_results = ()
    st.session_state.custom_search_query = ""
    st.session_state.custom_search_geocoding_unavailable = False
    st.session_state.custom_search_input = ""
    st.session_state.pop("custom_search_selection", None)


selection_mode = st.segmented_control(
    "Cách chọn địa điểm",
    ["Tìm địa danh", "Nhập tọa độ"],
    default="Tìm địa danh",
    key="custom_location_mode",
    on_change=clear_custom_search,
    persist_state="session",
)

if selection_mode == "Tìm địa danh":
    with st.form("custom_location_search", border=False):
        query = st.text_input(
            "Tên địa danh tại Việt Nam",
            placeholder="Ví dụ: Hội An, Sa Pa, Phú Quốc",
            key="custom_search_input",
        )
        searched = st.form_submit_button("Tìm", icon=":material/search:")

    search_failed = False
    if searched:
        searched_query = query.strip()
        st.session_state.custom_search_query = searched_query
        st.session_state.custom_search_geocoding_unavailable = False
        st.session_state.pop("custom_search_selection", None)

        provinces = cached_provinces(str(database_path())).to_dict(orient="records")
        province_matches = match_provinces(provinces, searched_query)
        try:
            geocoded = list(cached_location_search(searched_query, 10))
        except (ValueError, httpx.HTTPError) as error:
            geocoded = []
            st.session_state.custom_search_geocoding_unavailable = True
            if not province_matches:
                search_failed = True
                st.error(f"Không thể tìm địa danh: {error}", icon=":material/location_off:")

        st.session_state.custom_search_results = tuple(
            merge_search_results(province_matches, geocoded, limit=10)
        )

    search_results = st.session_state.custom_search_results
    if searched and not search_results and not search_failed:
        st.info("Không tìm thấy địa danh phù hợp tại Việt Nam.")
    if search_results:
        searched_query = st.session_state.custom_search_query
        result_indexes = list(range(len(search_results)))
        selected_index = st.selectbox(
            f"Kết quả tìm kiếm cho «{searched_query}»",
            result_indexes,
            format_func=lambda index: search_results[index].label,
            key="custom_search_selection",
        )
        if st.session_state.custom_search_geocoding_unavailable:
            st.caption(
                "Kết quả từ danh mục tỉnh/thành vẫn khả dụng; phần tìm kiếm mở rộng "
                "hiện không khả dụng."
            )
        if st.button("Dùng địa điểm này", type="primary", icon=":material/add_location_alt:"):
            selected = search_results[selected_index]
            st.session_state.custom_location = {
                "name": selected.label,
                "latitude": selected.latitude,
                "longitude": selected.longitude,
                "selection_source": (
                    "Danh mục điểm đại diện 34 tỉnh/thành"
                    if selected.origin == "province_registry"
                    else "Open-Meteo Geocoding / GeoNames"
                ),
            }
else:
    with st.form("custom_coordinate_form"):
        name = st.text_input("Tên gợi nhớ", value="Địa điểm của tôi")
        latitude = st.number_input(
            "Vĩ độ",
            min_value=7.0,
            max_value=24.0,
            value=16.0544,
            step=0.0001,
            format="%.5f",
        )
        longitude = st.number_input(
            "Kinh độ",
            min_value=102.0,
            max_value=116.0,
            value=108.2022,
            step=0.0001,
            format="%.5f",
        )
        coordinate_submitted = st.form_submit_button(
            "Dùng tọa độ này",
            icon=":material/my_location:",
        )
    if coordinate_submitted:
        try:
            validate_vietnam_coordinates(float(latitude), float(longitude))
            if not name.strip():
                raise ValueError("tên gợi nhớ không được để trống")
        except ValueError as error:
            st.error(f"Tọa độ không hợp lệ: {error}", icon=":material/wrong_location:")
        else:
            st.session_state.custom_location = {
                "name": name.strip(),
                "latitude": float(latitude),
                "longitude": float(longitude),
                "selection_source": "Tọa độ do người dùng nhập",
            }

location = st.session_state.custom_location
if location is None:
    st.info(
        "Hãy tìm một địa danh hoặc nhập tọa độ để xem dự báo 24–72 giờ.",
        icon=":material/travel_explore:",
    )
    methodology_expander()
    st.stop()

latitude = float(location["latitude"])
longitude = float(location["longitude"])
nearest, distance_km = nearest_province(latitude, longitude)

with st.container(border=True):
    st.subheader(str(location["name"]))
    st.write(
        f"**Tọa độ yêu cầu:** {latitude:.5f}, {longitude:.5f}  \n"
        f"**Nguồn chọn vị trí:** {location['selection_source']}"
    )
    st.caption(
        f"Điểm tỉnh đại diện gần nhất: {nearest.display_name} ({distance_km:.1f} km). "
        "Đây chỉ là tham chiếu khoảng cách, không phải ánh xạ địa giới hành chính."
    )

horizon = st.segmented_control(
    "Khoảng dự báo",
    [24, 48, 72],
    default=72,
    format_func=lambda value: f"{value} giờ",
    key="custom_forecast_horizon",
    persist_state="session",
)

forecast_slot = st.container()
try:
    with st.spinner("Đang lấy dự báo mô hình cho tọa độ đã chọn…", show_time=True):
        forecast = cached_on_demand_forecast(latitude, longitude, int(horizon or 72))
except (ValueError, httpx.HTTPError) as error:
    forecast_slot.error(
        f"Không thể tải dự báo lúc này: {error}",
        icon=":material/cloud_off:",
    )
    methodology_expander()
    st.stop()

if forecast.empty:
    forecast_slot.warning(
        "Nguồn mô hình không trả về giờ dự báo trùng khớp cho tọa độ này.",
        icon=":material/cloud_off:",
    )
    methodology_expander()
    st.stop()

forecast["valid_at_utc"] = pd.to_datetime(forecast["valid_at_utc"], utc=True)
forecast["valid_at_local"] = pd.to_datetime(forecast["valid_at_local"])
now_utc = pd.Timestamp.now(tz="UTC")
current_index = (forecast["valid_at_utc"] - now_utc).abs().idxmin()
current = forecast.loc[current_index]

source_badges(current)
st.caption(
    f"Lần lấy forecast: {format_local_timestamp(current['forecast_issued_at_utc'])} · "
    f"{len(forecast)} giờ khả dụng · cache tối đa 15 phút"
)

st.subheader(str(current["decision_label"]))
st.write(str(current["decision_explanation"]))
# Through build_metric, like the other pages: an on-demand fetch is at least as
# likely to come back with a null pollutant as the warehouse is, and formatting it
# inline printed the literal text "nan µg/m³".
metric_row(
    [
        build_metric(
            "Điểm phù hợp ngoài trời", current.get("outdoor_score"), unit="/100", decimals=0
        ),
        build_metric("PM2.5 mô hình", current.get("pm25_ugm3"), unit="µg/m³"),
        build_metric("Cảm giác nhiệt", current.get("apparent_temperature_c"), unit="°C"),
        build_metric(
            "Khả năng mưa", current.get("precipitation_probability_pct"), unit="%", decimals=0
        ),
    ]
)

with st.container(border=True):
    st.subheader("Bụi và khí ô nhiễm")
    st.caption("Mỗi chất một khung với trục y độc lập. Không so sánh độ cao giữa các khung.")
    render_pollutant_panels(
        forecast,
        empty_message="Không có chất ô nhiễm nào đủ dữ liệu để vẽ.",
    )

with st.container(border=True):
    st.subheader("Điểm phù hợp ngoài trời")
    st.area_chart(
        forecast,
        x="valid_at_local",
        y="outdoor_score",
        x_label="Giờ Việt Nam",
        y_label="Điểm 0–100",
    )

st.subheader("Các khung giờ phù hợp hơn")
windows = forecast[forecast["valid_at_utc"] >= now_utc].nlargest(5, "outdoor_score").copy()
windows["valid_at_local"] = windows["valid_at_local"].dt.strftime("%H:%M %d/%m")
st.dataframe(
    windows[
        [
            "valid_at_local",
            "outdoor_score",
            "decision_label",
            "pm25_ugm3",
            "precipitation_probability_pct",
            "apparent_temperature_c",
            "confidence_level",
        ]
    ],
    hide_index=True,
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
    },
)

with st.expander("Kiểm tra tọa độ lưới mô hình"):
    st.write(
        f"Lưới không khí: {current['air_grid_latitude']:.5f}, {current['air_grid_longitude']:.5f}"
    )
    st.write(
        f"Lưới thời tiết: {current['weather_grid_latitude']:.5f}, "
        f"{current['weather_grid_longitude']:.5f}"
    )
    st.caption(
        "Tọa độ lưới có thể lệch vài kilomet so với điểm yêu cầu; chất lượng không khí "
        "tại Việt Nam sử dụng mô hình khu vực, không phải cảm biến tại vị trí."
    )

methodology_expander()
