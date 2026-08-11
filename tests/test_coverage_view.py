"""Coverage strip: a day with no rows must be visible, not absent.

The defect these cover was found by driving the History page in a browser, not by
the suite. A plain groupby can only describe days that have rows, so a day with no
data produced no group and the page concluded every day was complete. Nothing
raised; the page simply stated something false.
"""

from datetime import date

import pandas as pd
import pytest

from dashboard.view_models import build_coverage

LOCATION = "Tỉnh/thành"


def frame_for(days: dict[str, list[str]]) -> pd.DataFrame:
    """Build an hourly frame from {location: [ISO day, ...]}, 24 hours per named day."""

    rows = []
    for location, day_list in days.items():
        for day in day_list:
            for hour in range(24):
                rows.append(
                    {
                        LOCATION: location,
                        "observed_at_utc": pd.Timestamp(f"{day} {hour:02d}:00", tz="UTC"),
                    }
                )
    return pd.DataFrame(rows)


def test_day_without_any_row_is_reported_as_zero_hours():
    """The exact shape measured against the real warehouse: 2 days present, 12 selected."""

    data = frame_for({"Hà Nội": ["2026-07-27", "2026-08-07"]})

    coverage = build_coverage(data, date(2026, 7, 27), date(2026, 8, 7))

    assert len(coverage) == 12, "every selected day must appear, not only the populated ones"
    empty = coverage[coverage["hours_with_data"] == 0]
    assert len(empty) == 10
    assert set(empty["missing_hours"]) == {24}
    # This is the assertion that fails on the old implementation: it returned two
    # rows, both complete, so the page printed "every day has a full 24 hours".
    assert not coverage[coverage["missing_hours"] > 0].empty


def test_every_selected_location_gets_every_selected_day():
    data = frame_for({"Hà Nội": ["2026-07-27"], "Hồ Chí Minh": ["2026-07-28"]})

    coverage = build_coverage(data, date(2026, 7, 27), date(2026, 7, 29))

    assert len(coverage) == 6, "2 locations x 3 days"
    hanoi = coverage[coverage[LOCATION] == "Hà Nội"].set_index("data_date_utc")
    assert hanoi.loc[date(2026, 7, 27), "hours_with_data"] == 24
    assert hanoi.loc[date(2026, 7, 28), "hours_with_data"] == 0
    assert hanoi.loc[date(2026, 7, 29), "hours_with_data"] == 0


def test_partial_day_keeps_its_real_hour_count():
    rows = [
        {LOCATION: "Hà Nội", "observed_at_utc": pd.Timestamp(f"2026-07-27 {h:02d}:00", tz="UTC")}
        for h in range(5)
    ]

    coverage = build_coverage(pd.DataFrame(rows), date(2026, 7, 27), date(2026, 7, 27))

    assert coverage.loc[0, "hours_with_data"] == 5
    assert coverage.loc[0, "missing_hours"] == 19


def test_repeated_readings_in_one_hour_count_once():
    """station_count > 1 means several rows share an hour; coverage counts hours."""

    stamp = pd.Timestamp("2026-07-27 03:00", tz="UTC")
    rows = [{LOCATION: "Hà Nội", "observed_at_utc": stamp} for _ in range(4)]

    coverage = build_coverage(pd.DataFrame(rows), date(2026, 7, 27), date(2026, 7, 27))

    assert coverage.loc[0, "hours_with_data"] == 1


def test_selected_location_with_no_rows_anywhere_is_still_reported():
    """The location-axis twin of the missing-day defect, on the default filters.

    Measured against the real warehouse: History defaults to all three cities, PM2.5
    and observed, and Da Nang has no observed PM2.5 rows at all. Deriving the axis
    from the frame published a strip covering Ha Noi and Ho Chi Minh City and never
    mentioned Da Nang -- the city with the worst coverage of the three.
    """

    data = frame_for({"Hà Nội": ["2026-07-27"], "Hồ Chí Minh": ["2026-07-27"]})

    coverage = build_coverage(
        data,
        date(2026, 7, 27),
        date(2026, 7, 28),
        locations=["Hà Nội", "Hồ Chí Minh", "Đà Nẵng"],
    )

    # Fails on the frame-derived axis: it returns 4 rows and no Da Nang at all.
    assert len(coverage) == 6, "3 selected locations x 2 days"
    da_nang = coverage[coverage[LOCATION] == "Đà Nẵng"]
    assert len(da_nang) == 2
    assert set(da_nang["hours_with_data"]) == {0}
    assert set(da_nang["missing_hours"]) == {24}


def test_selected_locations_drive_the_axis_even_when_the_frame_has_others():
    """A location outside the selection must not be smuggled in by the frame."""

    data = frame_for({"Hà Nội": ["2026-07-27"], "Hồ Chí Minh": ["2026-07-27"]})

    coverage = build_coverage(data, date(2026, 7, 27), date(2026, 7, 27), locations=["Hà Nội"])

    assert list(coverage[LOCATION]) == ["Hà Nội"]


def test_empty_frame_with_a_selection_reports_every_selected_day_as_empty():
    coverage = build_coverage(
        pd.DataFrame(), date(2026, 7, 27), date(2026, 7, 28), locations=["Hà Nội"]
    )

    assert len(coverage) == 2
    assert set(coverage["hours_with_data"]) == {0}
    assert set(coverage["missing_hours"]) == {24}


def test_empty_frame_returns_the_expected_columns():
    coverage = build_coverage(pd.DataFrame(), date(2026, 7, 27), date(2026, 7, 28))

    assert coverage.empty
    assert list(coverage.columns) == [
        LOCATION,
        "data_date_utc",
        "hours_with_data",
        "missing_hours",
    ]


@pytest.mark.parametrize("hours", [1, 12, 23, 24])
def test_missing_hours_never_goes_negative_and_complements_present(hours: int):
    rows = [
        {LOCATION: "Hà Nội", "observed_at_utc": pd.Timestamp(f"2026-07-27 {h:02d}:00", tz="UTC")}
        for h in range(hours)
    ]

    coverage = build_coverage(pd.DataFrame(rows), date(2026, 7, 27), date(2026, 7, 27))

    assert coverage.loc[0, "hours_with_data"] + coverage.loc[0, "missing_hours"] == 24
    assert coverage.loc[0, "missing_hours"] >= 0
