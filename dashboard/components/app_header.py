"""Persistent header: where you are, how fresh the data is, and how to refresh it.

The header owns the *primary* location — the one the single-location pages read. It
deliberately does not try to be the only location control in the app: `compare.py`
selects three to five places at once and `custom_location.py` takes arbitrary
coordinates, so a single global selector cannot serve them. Those pages keep their
own control and this one stays the primary.

It also has to survive a missing warehouse. Rendering the header is not the right
place to stop the app, because a reader on the Trust or Pipeline health page needs
those pages precisely when the warehouse is broken.
"""

from __future__ import annotations

import streamlit as st

from dashboard.runtime import cached_current, database_path, province_options
from dashboard.view_models import freshness_view

PRODUCT_NAME = "Không khí & thời tiết Việt Nam"
SESSION_LOCATION_KEY = "selected_province"


def _render_freshness(location_key: str | None) -> None:
    if not location_key:
        return
    try:
        current = cached_current(str(database_path()), location_key)
    except Exception:  # noqa: BLE001 - a header must not be able to break a page
        st.caption("Chưa đọc được trạng thái dữ liệu.")
        return
    if current.empty:
        st.badge("Chưa có dữ liệu", icon=":material/help:", color="gray")
        return
    row = current.iloc[0]
    text, icon, color = freshness_view(row.get("freshness_status"), row.get("forecast_age_minutes"))
    st.badge(text, icon=icon, color=color)
    st.badge("Mô hình CAMS", icon=":material/model_training:", color="blue")


def app_header() -> str | None:
    """Render the shared header and return the primary location key, if resolvable."""

    path = database_path()
    st.caption(PRODUCT_NAME)

    if not path.exists():
        # Say it once, here, and let the page decide whether it can carry on.
        st.badge("Chưa có warehouse", icon=":material/database_off:", color="red")
        st.divider()
        return None

    try:
        keys, labels = province_options(path)
    except Exception:  # noqa: BLE001 - dim_province may not be built yet
        st.badge("Chưa có danh mục tỉnh/thành", icon=":material/build:", color="orange")
        st.divider()
        return None

    if not keys:
        st.divider()
        return None

    stored = st.session_state.get(SESSION_LOCATION_KEY, keys[0])
    index = keys.index(stored) if stored in keys else 0

    selector, status = st.columns([2, 3])
    with selector:
        selection = st.selectbox(
            "Địa điểm chính",
            keys,
            index=index,
            format_func=lambda value: labels.get(value, value),
            key="header_province",
            help="Các trang một địa điểm dùng lựa chọn này. Trang So sánh và Địa điểm "
            "tùy chọn có bộ chọn riêng.",
        )
    # Written back so the pages that already read session state keep working, and so
    # the choice survives navigation.
    st.session_state[SESSION_LOCATION_KEY] = selection

    with status:
        with st.container(horizontal=True, horizontal_alignment="left"):
            _render_freshness(selection)
            with st.popover("Làm mới", icon=":material/refresh:"):
                st.caption(
                    "Số liệu được cache 5 phút. Làm mới sẽ đọc lại warehouse; nó không "
                    "gọi API và không chạy pipeline."
                )
                if st.button("Đọc lại warehouse", icon=":material/sync:"):
                    # Scoped to the data caches. Clearing everything would also throw
                    # away the geocoding and on-demand results, which cost real API
                    # calls to rebuild.
                    cached_current.clear()
                    st.rerun()

    st.divider()
    return str(selection)
