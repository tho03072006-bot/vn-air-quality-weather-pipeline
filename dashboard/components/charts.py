"""Altair chart specs shared by the forecast and current-conditions pages.

Every series is drawn with the colour its pollutant carries in `view_models`, so a
pollutant looks the same everywhere. Charts here take an already-filtered DataFrame
and return a spec; they do not query, so a page can compose them freely.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.view_models import (
    PM25_REFERENCE_UGM3,
    POLLUTANT_SERIES,
    normalise_datetimes,
    pollutant_colour,
    pollutant_label,
)

TIME_AXIS = alt.Axis(title="Giờ Việt Nam", format="%H:%M %d/%m", labelAngle=-35)


def _chart_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Second line of defence for the datetime64[us] problem.

    The cached loaders already normalise, but a caller can build a frame by hand
    -- custom_location does, from dataclasses -- and the failure is silent: Vega
    logs a console warning and draws an empty plot, so nothing server-side notices.
    Normalising again here costs a copy and removes the whole class of bug from
    every chart in this module.
    """

    return normalise_datetimes(frame)


def _long_form(frame: pd.DataFrame) -> pd.DataFrame:
    """Melt the wide pollutant columns into one row per pollutant and hour."""

    rows = []
    for pollutant, column in POLLUTANT_SERIES:
        if column not in frame.columns:
            continue
        series = frame[["valid_at_local", column]].rename(columns={column: "concentration"})
        series = series.dropna(subset=["concentration"])
        if series.empty:
            continue
        series["pollutant"] = pollutant
        series["label"] = pollutant_label(pollutant)
        rows.append(series)
    if not rows:
        return pd.DataFrame(columns=["valid_at_local", "concentration", "pollutant", "label"])
    return pd.concat(rows, ignore_index=True)


def pollutant_panels(frame: pd.DataFrame) -> list[tuple[str, alt.Chart]]:
    """One independent chart per pollutant, in `POLLUTANT_SERIES` order.

    Each pollutant gets its own chart rather than one faceted spec. A shared axis
    was the original design and it hid the very thing the chart exists to show: O3
    runs an order of magnitude above NO2, so NO2 flattened to a line along the
    bottom. Faceting fixed that but introduced its own defect -- a Vega-Lite facet
    carries a fixed pixel width and does not shrink to its container, so the grid
    measured 641px inside a 327px column at a 390px viewport and the panels past
    the fold were unreachable: no ancestor scrolled, so they were simply gone.

    Separate single-view charts have no such width. Each is drawn with
    `width="stretch"` by `render_pollutant_panels`, so Vega sizes it to whatever
    column it lands in, at any viewport. Independence of the y scales -- the point
    of the redesign -- is now structural rather than a `resolve_scale` argument.
    """

    long_form = _long_form(_chart_frame(frame))
    if long_form.empty:
        return []
    panels: list[tuple[str, alt.Chart]] = []
    for pollutant, label in _ordered_labels(long_form):
        series = long_form[long_form["pollutant"] == pollutant]
        panels.append(
            (
                label,
                alt.Chart(series)
                .mark_line(interpolate="monotone", strokeWidth=2, color=pollutant_colour(pollutant))
                .encode(
                    x=alt.X("valid_at_local:T", axis=TIME_AXIS),
                    y=alt.Y("concentration:Q", title="µg/m³"),
                    tooltip=[
                        alt.Tooltip("valid_at_local:T", title="Giờ", format="%H:%M %d/%m"),
                        alt.Tooltip("label:N", title="Chất"),
                        alt.Tooltip("concentration:Q", title="Nồng độ", format=".1f"),
                    ],
                )
                .properties(height=150, title=label),
            )
        )
    return panels


def render_pollutant_panels(frame: pd.DataFrame, *, empty_message: str) -> None:
    """Lay the pollutant panels out two-up, stacking on a narrow viewport.

    `st.columns` collapses to a single stacked column below Streamlit's small-screen
    breakpoint, which is what makes this responsive without a media query or any
    injected CSS. Two columns keep the desktop reading experience the facet gave.
    """

    panels = pollutant_panels(frame)
    if not panels:
        st.caption(empty_message)
        return
    # A fresh column pair per row, rather than one st.columns(2) filled round-robin.
    # Both look identical on a wide screen, but when the columns collapse the
    # round-robin version stacks a whole column at a time -- PM2.5, NO2, SO2, then
    # PM10, O3, CO -- which is not the order POLLUTANT_SERIES declares. Pairing keeps
    # the declared order in both layouts.
    for start in range(0, len(panels), 2):
        columns = st.columns(2)
        for column, (_, chart) in zip(columns, panels[start : start + 2], strict=False):
            with column:
                st.altair_chart(chart, width="stretch")


def _ordered_labels(long_form: pd.DataFrame) -> list[tuple[str, str]]:
    seen = []
    for pollutant, _ in POLLUTANT_SERIES:
        subset = long_form[long_form["pollutant"] == pollutant]
        if not subset.empty:
            seen.append((pollutant, pollutant_label(pollutant)))
    return seen


def pm25_timeline(frame: pd.DataFrame, *, hours: int = 24) -> alt.LayerChart | None:
    """PM2.5 over the next N hours with the first Bang 2 breakpoint marked."""

    if "pm25_ugm3" not in frame.columns:
        return None
    window = _chart_frame(frame).head(hours).dropna(subset=["pm25_ugm3"])
    if window.empty:
        return None

    line = (
        alt.Chart(window)
        .mark_area(
            interpolate="monotone",
            line={"color": pollutant_colour("pm25"), "strokeWidth": 2},
            color=alt.Gradient(
                gradient="linear",
                stops=[
                    alt.GradientStop(color="#fee2e2", offset=0),
                    alt.GradientStop(color=pollutant_colour("pm25"), offset=1),
                ],
                x1=1,
                x2=1,
                y1=1,
                y2=0,
            ),
            opacity=0.5,
        )
        .encode(
            x=alt.X("valid_at_local:T", axis=TIME_AXIS),
            y=alt.Y("pm25_ugm3:Q", title="PM2.5 (µg/m³)"),
            tooltip=[
                alt.Tooltip("valid_at_local:T", title="Giờ", format="%H:%M %d/%m"),
                alt.Tooltip("pm25_ugm3:Q", title="PM2.5", format=".1f"),
                alt.Tooltip("outdoor_score:Q", title="Điểm ngoài trời", format=".0f"),
            ],
        )
    )
    # A reference line rather than a coloured background: the reader needs to see
    # whether the curve crosses the threshold, not have the whole panel tinted.
    threshold = (
        alt.Chart(pd.DataFrame({"y": [PM25_REFERENCE_UGM3]}))
        .mark_rule(strokeDash=[6, 4], color="#64748b")
        .encode(y="y:Q")
    )
    caption = (
        alt.Chart(pd.DataFrame({"y": [PM25_REFERENCE_UGM3], "text": ["Ngưỡng 25 µg/m³ (Bảng 2)"]}))
        .mark_text(align="left", baseline="bottom", dx=6, dy=-4, fontSize=11, color="#64748b")
        .encode(y="y:Q", text="text:N")
    )
    return (line + threshold + caption).properties(height=220)


def weather_panel(frame: pd.DataFrame) -> alt.Chart | None:
    """Temperature and apparent temperature, which do share a unit and a scale."""

    columns = [c for c in ("temperature_2m_c", "apparent_temperature_c") if c in frame.columns]
    if not columns:
        return None
    melted = (
        _chart_frame(frame)
        .melt(id_vars="valid_at_local", value_vars=columns, var_name="series", value_name="celsius")
        .dropna(subset=["celsius"])
    )
    if melted.empty:
        return None
    labels = {"temperature_2m_c": "Nhiệt độ", "apparent_temperature_c": "Cảm giác nhiệt"}
    melted["series"] = melted["series"].map(labels)
    return (
        alt.Chart(melted)
        .mark_line(interpolate="monotone", strokeWidth=2)
        .encode(
            x=alt.X("valid_at_local:T", axis=TIME_AXIS),
            y=alt.Y("celsius:Q", title="°C"),
            color=alt.Color(
                "series:N",
                title=None,
                scale=alt.Scale(
                    domain=["Nhiệt độ", "Cảm giác nhiệt"], range=["#0f766e", "#f59e0b"]
                ),
            ),
            tooltip=[
                alt.Tooltip("valid_at_local:T", title="Giờ", format="%H:%M %d/%m"),
                alt.Tooltip("series:N", title=None),
                alt.Tooltip("celsius:Q", title="°C", format=".1f"),
            ],
        )
        .properties(height=220)
    )


def precipitation_panel(frame: pd.DataFrame) -> alt.Chart | None:
    """Rain probability alone.

    Previously this shared an axis with the UV index. Percent and a 0-11 index have
    neither the same unit nor the same range, so one curve was decorative and the
    other unreadable. UV gets its own panel.
    """

    if "precipitation_probability_pct" not in frame.columns:
        return None
    window = _chart_frame(frame).dropna(subset=["precipitation_probability_pct"])
    if window.empty:
        return None
    return (
        alt.Chart(window)
        .mark_bar(color="#2563eb", opacity=0.75)
        .encode(
            x=alt.X("valid_at_local:T", axis=TIME_AXIS),
            y=alt.Y(
                "precipitation_probability_pct:Q",
                title="Khả năng mưa (%)",
                scale=alt.Scale(domain=[0, 100]),
            ),
            tooltip=[
                alt.Tooltip("valid_at_local:T", title="Giờ", format="%H:%M %d/%m"),
                alt.Tooltip("precipitation_probability_pct:Q", title="Mưa", format=".0f"),
                alt.Tooltip("precipitation_mm:Q", title="Lượng mưa (mm)", format=".1f"),
            ],
        )
        .properties(height=200)
    )


def uv_panel(frame: pd.DataFrame) -> alt.Chart | None:
    if "uv_index" not in frame.columns:
        return None
    window = _chart_frame(frame).dropna(subset=["uv_index"])
    if window.empty:
        return None
    return (
        alt.Chart(window)
        .mark_line(interpolate="monotone", strokeWidth=2, color="#d97706")
        .encode(
            x=alt.X("valid_at_local:T", axis=TIME_AXIS),
            y=alt.Y("uv_index:Q", title="Chỉ số UV"),
            tooltip=[
                alt.Tooltip("valid_at_local:T", title="Giờ", format="%H:%M %d/%m"),
                alt.Tooltip("uv_index:Q", title="UV", format=".1f"),
            ],
        )
        .properties(height=200)
    )


def render(chart: alt.Chart | alt.LayerChart | None, *, empty_message: str) -> None:
    """Draw a chart, or say plainly that the data for it is absent."""

    if chart is None:
        st.caption(empty_message)
        return
    st.altair_chart(chart, width="stretch")
