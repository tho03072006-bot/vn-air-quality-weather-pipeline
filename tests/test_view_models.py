"""Formatting rules for the dashboard, tested without a Streamlit runtime.

These exist because the serving mart genuinely emits nulls -- it left-joins weather
and its own confidence_level branches on those columns being null -- and the pages
used to format them inline, so a missing reading reached the user as "nan µg/m³".
"""

import math

import pandas as pd
import pytest

from dashboard.view_models import (
    MISSING_DISPLAY,
    build_metric,
    dominant_pollutant,
    format_age,
    format_number,
    freshness_view,
    is_missing,
    normalise_datetimes,
)


@pytest.mark.parametrize(
    "value",
    [None, float("nan"), pd.NA, pd.NaT],
    ids=["none", "nan", "pandas-na", "pandas-nat"],
)
def test_every_flavour_of_missing_is_detected(value: object) -> None:
    # The warehouse reaches the page through pandas, which uses several distinct
    # null sentinels depending on column dtype.
    assert is_missing(value) is True


@pytest.mark.parametrize("value", [0, 0.0, -3.5, 12.25, "0"])
def test_present_values_are_not_treated_as_missing(value: object) -> None:
    # Zero is a real measurement. Treating it as missing would hide clean air.
    assert is_missing(value) is False


def test_missing_measurements_render_as_a_marker_not_nan() -> None:
    assert format_number(None, unit="µg/m³") == MISSING_DISPLAY
    assert format_number(float("nan"), unit="µg/m³") == MISSING_DISPLAY
    assert "nan" not in format_number(float("nan"), unit="µg/m³")


def test_measurements_keep_their_unit_and_precision() -> None:
    assert format_number(12.34, unit="µg/m³") == "12.3 µg/m³"
    assert format_number(65.5, unit="%", decimals=0) == "66 %"
    assert format_number(7, decimals=0) == "7"


def test_infinite_values_are_not_published() -> None:
    # An infinity means an upstream division went wrong; showing it as a reading
    # would be worse than admitting the value is unusable.
    assert format_number(math.inf, unit="µg/m³") == MISSING_DISPLAY


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0, "0 phút"),
        (45, "45 phút"),
        (60, "1 giờ"),
        (57, "57 phút"),
        (135, "2 giờ 15 phút"),
        (1440, "1 ngày"),
        (1500, "1 ngày 1 giờ"),
        (-3, "chưa tới"),
        (None, "chưa xác định"),
    ],
)
def test_age_is_rendered_the_way_a_person_says_it(minutes: object, expected: str) -> None:
    assert format_age(minutes) == expected


def test_a_metric_with_no_value_explains_itself() -> None:
    metric = build_metric("PM2.5", None, unit="µg/m³")

    assert metric.value == MISSING_DISPLAY
    assert metric.is_available is False
    # Without this the tile is a bare dash and the reader cannot tell a broken
    # dashboard from an hour the provider did not cover.
    assert metric.help_text is not None


def test_a_metric_with_a_value_carries_no_apology() -> None:
    metric = build_metric("PM2.5", 18.42, unit="µg/m³")

    assert metric.value == "18.4"
    assert metric.is_available is True
    assert metric.help_text is None


def test_the_unit_lives_in_the_label_so_the_number_is_never_truncated() -> None:
    # Streamlit truncates an overflowing metric value with an ellipsis instead of
    # wrapping. With the unit inside the value, a four-column row rendered
    # "75.7 µg/m³" as "75…" and cut off the number itself. The label wraps, so the
    # unit belongs there.
    metric = build_metric("PM2.5 mô hình", 75.7, unit="µg/m³")

    assert metric.value == "75.7"
    assert metric.label == "PM2.5 mô hình (µg/m³)"
    assert "…" not in metric.value


def test_a_suffix_style_unit_is_not_bracketed() -> None:
    metric = build_metric("Điểm phù hợp ngoài trời", 29, unit="/100", decimals=0)

    assert metric.value == "29"
    assert metric.label == "Điểm phù hợp ngoài trời /100"


def test_highest_concentration_ignores_missing_pollutants() -> None:
    assert dominant_pollutant({"pm25": 30.0, "pm10": None, "o3": 45.0}) == "o3"


def test_highest_concentration_is_absent_when_nothing_was_measured() -> None:
    assert dominant_pollutant({"pm25": None, "pm10": float("nan")}) is None


def test_freshness_carries_text_and_icon_not_just_colour() -> None:
    text, icon, color = freshness_view("DELAYED", 480)

    assert "chậm" in text.lower()
    assert "8 giờ" in text
    assert icon
    assert color == "orange"


def test_unknown_freshness_degrades_to_the_most_cautious_state() -> None:
    # Guessing FRESH for an unrecognised status would overstate the data.
    _, _, color = freshness_view(None, None)
    assert color == "red"


def test_microsecond_timestamps_are_cast_to_nanoseconds() -> None:
    # DuckDB returns datetime64[us]. Vega cannot read Altair's serialisation of
    # that unit, so every chart built on a raw query result silently drew an empty
    # plot: the axis domain collapsed to [Infinity, -Infinity] and only a browser
    # console warning said so. Server-side tests that checked the surrounding
    # caption all passed while the chart showed nothing.
    frame = pd.DataFrame(
        {"valid_at_local": pd.to_datetime(["2026-08-09 07:00", "2026-08-09 08:00"])}
    ).astype({"valid_at_local": "datetime64[us]"})
    assert frame["valid_at_local"].dtype == "datetime64[us]"

    converted = normalise_datetimes(frame)

    assert converted["valid_at_local"].dtype == "datetime64[ns]"


def test_normalising_preserves_the_timezone_pages_display() -> None:
    # The pages deliberately show Vietnam local time; stripping the zone here would
    # shift every label by seven hours.
    frame = pd.DataFrame(
        {"observed_at_local": pd.to_datetime(["2026-08-09 07:00+07:00"]).as_unit("us")}
    )

    converted = normalise_datetimes(frame)

    assert str(converted["observed_at_local"].dtype.tz) == "UTC+07:00"
    assert converted["observed_at_local"].dtype.unit == "ns"


def test_normalising_leaves_non_datetime_columns_alone() -> None:
    frame = pd.DataFrame({"pm25_ugm3": [12.5, None], "city": ["hanoi", "hue"]})

    converted = normalise_datetimes(frame)

    assert converted["pm25_ugm3"].dtype == "float64"
    assert converted["city"].tolist() == ["hanoi", "hue"]


def test_normalising_does_not_mutate_the_caller_frame() -> None:
    # The loaders are cached, so mutating in place would corrupt the cache entry.
    frame = pd.DataFrame({"valid_at_local": pd.to_datetime(["2026-08-09 07:00"])}).astype(
        {"valid_at_local": "datetime64[us]"}
    )

    normalise_datetimes(frame)

    assert frame["valid_at_local"].dtype == "datetime64[us]"
