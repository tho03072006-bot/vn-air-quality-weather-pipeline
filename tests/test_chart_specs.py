"""Pollutant panels must carry no fixed pixel width.

The facet this replaced measured 641px inside a 327px column at a 390px viewport,
and no ancestor scrolled, so the panels past the fold were unreachable rather than
merely awkward. A fixed `width` in the spec is the thing that caused it, so that is
what these assert against -- a spec cannot be responsive and carry one.
"""

import pandas as pd
import pytest

from dashboard.components.charts import pollutant_panels
from dashboard.view_models import POLLUTANT_SERIES


@pytest.fixture
def forecast_frame() -> pd.DataFrame:
    hours = pd.date_range("2026-08-09 07:00", periods=12, freq="h")
    frame = pd.DataFrame({"valid_at_local": hours})
    for index, (_, column) in enumerate(POLLUTANT_SERIES):
        frame[column] = [float(index * 10 + hour) for hour in range(len(hours))]
    return frame


def widths(spec: dict) -> list:
    """Every `width` key anywhere in the spec tree."""

    found = []
    if isinstance(spec, dict):
        for key, value in spec.items():
            if key == "width":
                found.append(value)
            found.extend(widths(value))
    elif isinstance(spec, list):
        for item in spec:
            found.extend(widths(item))
    return found


def test_one_panel_per_pollutant_present(forecast_frame):
    panels = pollutant_panels(forecast_frame)

    assert [label for label, _ in panels] == ["PM2.5", "PM10", "NO₂", "O₃", "SO₂", "CO"]


def test_no_panel_declares_a_fixed_pixel_width(forecast_frame):
    """Fails on the old facet spec, which declared width 240 on the child view."""

    for label, chart in pollutant_panels(forecast_frame):
        numeric = [w for w in widths(chart.to_dict()) if isinstance(w, int | float)]
        assert not numeric, f"{label} pins a pixel width ({numeric}); it cannot then shrink"


def test_each_panel_holds_only_its_own_pollutant(forecast_frame):
    """Independent y scales are structural now, so each panel must be single-series."""

    for label, chart in pollutant_panels(forecast_frame):
        data = chart.data
        assert set(data["label"]) == {label}


def test_panels_omit_pollutants_with_no_data(forecast_frame):
    forecast_frame["so2_ugm3"] = None

    labels = [label for label, _ in pollutant_panels(forecast_frame)]

    assert "SO₂" not in labels
    assert "PM2.5" in labels


def test_no_panels_when_nothing_has_data():
    empty = pd.DataFrame({"valid_at_local": pd.date_range("2026-08-09", periods=3, freq="h")})

    assert pollutant_panels(empty) == []
