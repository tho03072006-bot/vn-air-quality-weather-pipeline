"""Entry point for the Vietnam air-quality and weather decision platform."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

st.set_page_config(
    page_title="Không khí & thời tiết Việt Nam",
    page_icon=":material/air:",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "selected_province" not in st.session_state:
    st.session_state.selected_province = "hanoi"

APP_PAGES = Path(__file__).resolve().parent / "app_pages"

navigation = st.navigation(
    {
        "Ra quyết định": [
            st.Page(
                APP_PAGES / "today.py",
                title="Hôm nay",
                icon=":material/today:",
                default=True,
            ),
            st.Page(
                APP_PAGES / "forecast.py",
                title="Dự báo 24–72 giờ",
                icon=":material/timeline:",
            ),
            st.Page(
                APP_PAGES / "custom_location.py",
                title="Địa điểm tùy chọn",
                icon=":material/add_location_alt:",
            ),
            st.Page(
                APP_PAGES / "national_map.py",
                title="Bản đồ Việt Nam",
                icon=":material/map:",
            ),
            st.Page(
                APP_PAGES / "compare.py",
                title="So sánh địa điểm",
                icon=":material/compare_arrows:",
            ),
        ],
        "Phân tích & tin cậy": [
            st.Page(
                APP_PAGES / "history.py",
                title="Lịch sử",
                icon=":material/history:",
            ),
            st.Page(
                APP_PAGES / "alerts.py",
                title="Cảnh báo",
                icon=":material/notifications:",
            ),
            st.Page(
                APP_PAGES / "trust.py",
                title="Độ tin cậy dữ liệu",
                icon=":material/fact_check:",
            ),
            st.Page(
                APP_PAGES / "pipeline_health.py",
                title="Pipeline health",
                icon=":material/monitor_heart:",
            ),
        ],
    }
)

with st.sidebar:
    st.caption("Vietnam Air Quality & Weather Decision Platform")
    st.caption("34/34 tỉnh/thành · hỗ trợ tọa độ tùy chọn")

# Rendered before navigation.run(), so it appears once on every page rather than
# being repeated in nine page scripts. Imported here rather than at module top
# because it reaches into runtime, which builds cached resources.
from dashboard.components.app_header import app_header  # noqa: E402

app_header()

navigation.run()
