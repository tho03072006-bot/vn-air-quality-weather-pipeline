"""Source, coverage, confidence and freshness badges.

Every badge carries text and an icon as well as a colour. A reader with a colour
vision deficiency, or one looking at a greyscale print, has to get the same signal.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.view_models import COVERAGE_LABELS, freshness_view, is_missing
from vn_air_quality_weather.sources import SOURCES

# Presentation only. The registry classifies sources; naming those classes for a
# reader is this layer's job, and putting the Vietnamese here keeps the registry
# free of display concerns and safe to import from a pipeline or a test.
KIND_LABELS = {
    "observed_station": "Quan trắc tại trạm",
    "model_reanalysis": "Mô hình tái phân tích",
    "model_forecast": "Mô hình dự báo",
    "satellite_column": "Cột vệ tinh",
}


def freshness_badge(row: pd.Series) -> None:
    """State how old the underlying forecast vintage is.

    The serving view grades this against the six-hourly ingest cadence, so DELAYED
    means a cycle was actually missed rather than that the clock has moved on since
    the last dbt build.
    """

    text, icon, color = freshness_view(row.get("freshness_status"), row.get("forecast_age_minutes"))
    st.badge(text, icon=icon, color=color)


def source_badges(row: pd.Series) -> None:
    """Show where the number came from and how much to trust it."""

    with st.container(horizontal=True, horizontal_alignment="left"):
        st.badge("Mô hình CAMS", icon=":material/model_training:", color="blue")
        coverage = str(row.get("coverage_tier"))
        st.badge(
            COVERAGE_LABELS.get(coverage, coverage),
            icon=":material/radar:",
            color="orange",
        )
        confidence = str(row.get("confidence_level", "LOW"))
        st.badge(
            f"Độ tin cậy {confidence.lower()}",
            icon=":material/verified:",
            color="green" if confidence == "MEDIUM" else "gray",
        )
        # Only shown when it applies. A badge that is always present is ignored;
        # one that appears only on mixed-vintage rows is information.
        aligned = row.get("is_vintage_aligned")
        if not is_missing(aligned) and not bool(aligned):
            st.badge(
                "Air và weather khác lần chạy mô hình",
                icon=":material/call_split:",
                color="red",
            )


def source_registry() -> None:
    """List every upstream source, and say that its licence is unverified.

    Read from `vn_air_quality_weather.sources` rather than restated here. That
    registry existed with no consumer at all: it was written to hold provider and
    licence metadata, nothing imported it, and the Trust page described the same
    sources in prose beside it. Two descriptions of one fact is how the outdoor-window
    and empirical-comparison claims went stale (finding P), so the prose is now
    generated from the registry and there is one copy again.

    Rendered as markdown, deliberately, not as a dataframe. Streamlit draws a
    dataframe on a canvas, which puts its text beyond the reach of a screen reader
    and beyond the reach of the gate that keeps this disclosure honest. A disclosure
    no check can assert is a disclosure that can quietly disappear.

    The licence line is the part that matters. Every entry is registered UNVERIFIED,
    and this app is now published publicly -- so the reader is told that the terms
    have not been confirmed, rather than being left to assume they have.
    """

    lines = []
    for source in SOURCES.values():
        kind = KIND_LABELS.get(source.measurement_kind, source.measurement_kind)
        lines.append(
            f"- **{source.display_name}** — {source.provider} · {kind} · "
            f"giấy phép: `{source.licence}`"
        )
    st.markdown("\n".join(lines))
    # Says what UNVERIFIED means without counting how many carry it. A sentence like
    # "all four sources are unverified" would be a second copy of the registry's
    # contents, wrong the moment a source is added or a licence confirmed, and this
    # component exists precisely because that pattern went wrong twice already.
    st.caption(
        "Trạng thái giấy phép ở trên đọc thẳng từ registry nguồn. `UNVERIFIED` nghĩa là "
        "điều khoản giấy phép, ghi công và tái phân phối **chưa được con người xác nhận** "
        "— sản phẩm đang publish công khai nên điều đó được nói ra, thay vì để người đọc "
        "mặc định là đã kiểm tra xong. Mặc định vận hành là luôn ghi công nguồn."
    )
