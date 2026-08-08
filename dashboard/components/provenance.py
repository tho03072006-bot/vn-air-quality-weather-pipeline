"""Source, coverage, confidence and freshness badges.

Every badge carries text and an icon as well as a colour. A reader with a colour
vision deficiency, or one looking at a greyscale print, has to get the same signal.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.view_models import COVERAGE_LABELS, freshness_view, is_missing


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
