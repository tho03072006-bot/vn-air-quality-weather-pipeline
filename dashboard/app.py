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

# Let a KPI label wrap instead of being cut off with an ellipsis.
#
# Streamlit styles the metric label `white-space: nowrap; text-overflow: ellipsis`,
# so in a four-column KPI row it silently truncates. That is how "PM2.5 mô hình
# trung vị (µg/m³)" -- 183px of text in a 161px box -- reached the browser as
# "PM2.5 mô hình trung vị (µg…". The unit had been moved out of the value and into
# the label precisely to stop the *value* being truncated, on the belief that labels
# wrap. They do not, so the fix relocated the defect rather than removing it.
#
# CSS rather than shorter wording because shorter wording fixes three labels while
# this fixes the class: any label, any future page, any viewport. Found by
# scripts/verify_layout.py, which is what now keeps it fixed.
#
# Streamlit's semantic badge and alert backgrounds are intentionally pale, but the
# foregrounds it derives for red, orange, green and gray do not reach 4.5:1 on those
# backgrounds. Match the stable component selectors and the badge's semantic
# background token, then replace only the foreground. Matching the background keeps
# the blue CAMS badge out of the override and leaves every component background
# untouched. Descendant selectors cover Material Symbols as well as normal text.
st.html(
    """
    <style>
      [data-testid="stMetricLabel"] * {
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
      }

      /* Guarantee a visible keyboard focus indicator everywhere (WCAG 2.4.7).
         Streamlit rings most of its widgets but not all: measured with
         scripts/verify_keyboard.py, the date-range input, the multiselect input and
         the chart canvas elements looked identical focused and unfocused under a real
         Tab press, so a keyboard reader could not tell where they were.
         `:focus-visible` rather than `:focus` keeps the ring off a mouse click, and
         one blanket rule rather than three selectors covers any widget added later.

         NEVER write an angle-bracketed tag name inside this block, not even in a
         comment. st.html sanitises its argument as markup, so a literal tag name
         terminates the style element and silently discards every rule after it --
         which is how this very rule, and the badge colours below it, went missing
         from the page while remaining present in this file. */
      :focus-visible {
        outline: 2px solid #0F766E !important;
        outline-offset: 2px !important;
      }

      span.stMarkdownBadge[style*="background-color: rgba(255, 43, 43, 0.1)"],
      span.stMarkdownBadge[style*="background-color: rgba(255, 43, 43, 0.1)"] * {
        color: #882e30 !important;
      }

      span.stMarkdownBadge[style*="background-color: rgba(255, 164, 33, 0.1)"],
      span.stMarkdownBadge[style*="background-color: rgba(255, 164, 33, 0.1)"] * {
        color: #853c07 !important;
      }

      span.stMarkdownBadge[style*="background-color: rgba(33, 195, 84, 0.1)"],
      span.stMarkdownBadge[style*="background-color: rgba(33, 195, 84, 0.1)"] * {
        color: #0f5c27 !important;
      }

      span.stMarkdownBadge[style*="background-color: rgba(49, 51, 63, 0.1)"],
      span.stMarkdownBadge[style*="background-color: rgba(49, 51, 63, 0.1)"] * {
        color: #494a4f !important;
      }

      div[data-testid="stAlertContentError"],
      div[data-testid="stAlertContentError"] *,
      div[data-testid="stAlertContentError"] span[data-testid="stAlertDynamicIcon"] {
        color: #882e30 !important;
      }

      div[data-testid="stAlertContentWarning"],
      div[data-testid="stAlertContentWarning"] *,
      div[data-testid="stAlertContentWarning"] span[data-testid="stAlertDynamicIcon"] {
        color: #853c07 !important;
      }

      div[data-testid="stAlertContentSuccess"],
      div[data-testid="stAlertContentSuccess"] *,
      div[data-testid="stAlertContentSuccess"] span[data-testid="stAlertDynamicIcon"] {
        color: #0f5c27 !important;
      }
    </style>
    """
)

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
