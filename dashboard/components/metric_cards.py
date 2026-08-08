"""KPI row that survives missing readings."""

from __future__ import annotations

import streamlit as st

from dashboard.view_models import MetricView

# Four is the cap. Past that the tiles compete rather than rank, and on a phone
# they stack into a wall of numbers with no visible hierarchy.
MAX_METRIC_COLUMNS = 4


def metric_row(metrics: list[MetricView]) -> None:
    """Render up to four KPI tiles, showing absence as absence.

    A tile whose value is missing still occupies its slot. Dropping it would
    reshuffle the row and make two visits to the same page disagree about which
    number sits where, which is worse for a reader than an honest blank.
    """

    if not metrics:
        return
    visible = metrics[:MAX_METRIC_COLUMNS]
    columns = st.columns(len(visible))
    for column, metric in zip(columns, visible, strict=True):
        column.metric(
            metric.label,
            metric.value,
            help=metric.help_text,
            border=True,
        )
