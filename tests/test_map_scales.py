"""Map colour banding, tested away from PyDeck and the browser.

The legend and the marker fills are both read from these bands. If they were
derived separately they would drift the first time a threshold moved, and a map
whose key disagrees with its markers is worse than one with no key at all.
"""

import pandas as pd
import pytest

from dashboard.view_models import (
    MAP_METRICS,
    MISSING_RGB,
    OUTDOOR_METRIC,
    PM25_METRIC,
    RAIN_METRIC,
    band_colour,
    band_for,
    marker_radius,
    metric_by_key,
)


def test_every_metric_ramp_terminates() -> None:
    # A final band with a numeric upper bound would leave extreme values unmapped,
    # and band_for would fall through to a silent default.
    for metric in MAP_METRICS:
        assert metric.bands, f"{metric.key} has no bands"
        assert metric.bands[-1].upper is None, f"{metric.key} ramp is not open-ended"


@pytest.mark.parametrize(
    ("value", "expected_label"),
    [
        (0.0, "0–25"),
        (24.9, "0–25"),
        (25.0, "25–50"),
        (79.9, "50–80"),
        (150.0, "150–250"),
        (900.0, "> 250"),
    ],
)
def test_pm25_thresholds_match_their_printed_ranges(value: float, expected_label: str) -> None:
    # Upper bounds are exclusive, so a value sitting exactly on a threshold belongs
    # to the band whose printed range starts with it.
    band = band_for(value, PM25_METRIC)
    assert band is not None
    assert band.label == expected_label


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA, "not a number"])
def test_anchors_without_a_reading_are_grey_not_low(value: object) -> None:
    # Painting a missing anchor with the low end of the ramp would read as clean air.
    assert band_for(value, PM25_METRIC) is None
    assert band_colour(value, PM25_METRIC) == MISSING_RGB


def test_missing_colour_is_not_reused_by_any_band() -> None:
    for metric in MAP_METRICS:
        assert MISSING_RGB not in {band.rgb for band in metric.bands}


def test_outdoor_score_bands_agree_with_the_marts_decision_labels() -> None:
    # mart_location_hourly_forecast cuts decision_label at 70 and 45. If these drift
    # apart, one row shows a green marker beside the words "Nên hạn chế".
    assert band_for(80.0, OUTDOOR_METRIC).label.startswith("Phù hợp hơn")
    assert band_for(50.0, OUTDOOR_METRIC).label.startswith("Cân nhắc")
    assert band_for(20.0, OUTDOOR_METRIC).label.startswith("Nên hạn chế")


def test_radius_grows_with_severity_not_with_the_raw_number() -> None:
    # Sizing by raw value let one polluted anchor swamp the map.
    clean = marker_radius(5.0, PM25_METRIC)
    dirty = marker_radius(300.0, PM25_METRIC)
    assert dirty > clean
    # A 1 µg/m³ difference inside one band must not change the marker at all.
    assert marker_radius(10.0, PM25_METRIC) == marker_radius(11.0, PM25_METRIC)


def test_radius_respects_metrics_where_high_is_good() -> None:
    # A high outdoor score is the good case, so it must not draw the loudest marker.
    assert marker_radius(90.0, OUTDOOR_METRIC) < marker_radius(10.0, OUTDOOR_METRIC)


def test_missing_anchors_draw_the_smallest_marker() -> None:
    absent = marker_radius(None, PM25_METRIC)
    assert absent < marker_radius(1.0, PM25_METRIC)


def test_metric_lookup_falls_back_rather_than_raising() -> None:
    # The segmented control can return None on first render.
    assert metric_by_key("pm25") is PM25_METRIC
    assert metric_by_key("rain") is RAIN_METRIC
    assert metric_by_key("nonsense") is PM25_METRIC


def test_metric_columns_are_distinct_and_named() -> None:
    keys = [metric.key for metric in MAP_METRICS]
    assert len(keys) == len(set(keys))
    for metric in MAP_METRICS:
        assert metric.label
        assert metric.legend_note
