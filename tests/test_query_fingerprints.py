"""Cover the fingerprint canonicalisation, and the pinned window it depends on.

These are not incidental helpers. `scripts/export_query_fingerprints.py` produces the
fixture that the Java repositories are checked against, so a defect in its
canonicalisation does not fail loudly -- it reports a disagreement that is not there,
or hides one that is. Both happened, and both are pinned below.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from dashboard.data_access import load_location_forecast
from scripts.export_query_fingerprints import canonical, is_missing

UTC = dt.UTC
BANGKOK = dt.timezone(dt.timedelta(hours=7))


# --- missing values ---------------------------------------------------------


@pytest.mark.parametrize("value", [None, float("nan"), pd.NaT, pd.NA])
def test_every_flavour_of_missing_is_null(value: object) -> None:
    """Regression: `pd.NA` was read as the literal string `<NA>`.

    DuckDB returns a nullable dtype for an INTEGER column that has nulls, so its
    missing values arrive as `pd.NA` rather than as NaN. Falling through to the string
    branch recorded three absent AQI sub-indices as three present ones, and made the
    Java side -- which reported them correctly as null -- look like the broken one.
    """

    assert is_missing(value)
    assert canonical(value) == "NULL"


def test_a_container_is_not_mistaken_for_a_missing_value() -> None:
    # pd.isna answers element-wise for a container and raises when asked for a single
    # truth, so the call has to be guarded rather than trusted.
    assert not is_missing([1, 2])
    assert not is_missing("")


# --- numbers ----------------------------------------------------------------


def test_an_integral_double_is_spelled_as_an_integer() -> None:
    """Regression: pandas widens a 128-bit integer column to float64.

    `paired_hours` is a HUGEINT holding 275; NumPy has no such type, so it reaches the
    fingerprint as 275.0 while Java reads the same column as a long. Recording
    `f:275.000000` disagreed with `i:275` over a difference that exists only in the
    container.
    """

    assert canonical(275.0) == "i:275"
    assert canonical(275) == "i:275"
    assert canonical(0.0) == "i:0"


def test_a_fractional_double_keeps_its_fraction() -> None:
    # The check that survives the rule above: a field that read 68.7 as an integer
    # would render i:68 against this, and still be caught.
    assert canonical(68.7) == "f:68.700000"
    assert canonical(13.65) == "f:13.650000"


def test_a_double_too_large_to_be_exactly_integral_keeps_its_float_form() -> None:
    # Past 2^53 a double no longer represents every integer, so "integral" stops being
    # a fact about the value and becomes an artefact of the representation.
    assert canonical(2.0**53).startswith("f:")


def test_booleans_are_not_integers() -> None:
    assert canonical(True) == "true"
    assert canonical(False) == "false"
    assert canonical(1) == "i:1"


# --- instants ---------------------------------------------------------------


def test_one_instant_has_one_spelling_whatever_offset_it_arrives_in() -> None:
    assert canonical(pd.Timestamp("2026-08-16T13:00:00.714908", tz=BANGKOK)) == canonical(
        pd.Timestamp("2026-08-16T06:00:00.714908", tz=UTC)
    )


def test_a_naive_timestamp_is_never_confused_with_an_instant() -> None:
    naive = canonical(pd.Timestamp("2026-08-19T12:00:00"))
    aware = canonical(pd.Timestamp("2026-08-19T12:00:00", tz=UTC))
    assert naive.startswith("naive:")
    assert aware.startswith("utc:")
    assert naive != aware


# --- the pinned forecast window ---------------------------------------------


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    """A three-hour forecast for one location, and nothing else.

    Hand-built rather than taken from the demo warehouse: this exercises the window
    predicate, and a fixture whose hours are chosen here is one whose expected answer
    can be written down.
    """

    database = tmp_path / "window.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("create schema analytics")
        connection.execute(
            """
            create table analytics.mart_location_hourly_forecast (
                location_key varchar,
                valid_at_utc timestamptz
            )
            """
        )
        connection.execute(
            """
            insert into analytics.mart_location_hourly_forecast values
                ('hanoi', timestamptz '2026-08-16 04:00:00+00'),
                ('hanoi', timestamptz '2026-08-16 05:00:00+00'),
                ('hanoi', timestamptz '2026-08-16 06:00:00+00'),
                ('hanoi', timestamptz '2026-08-16 07:00:00+00'),
                ('hanoi', timestamptz '2026-08-16 08:00:00+00'),
                ('hue',   timestamptz '2026-08-16 06:00:00+00')
            """
        )
    return database


def test_the_pinned_window_starts_one_hour_before_the_anchor(warehouse: Path) -> None:
    """The hour in progress is included, not dropped.

    A window that began at the current hour boundary would lose the hour a reader is
    standing in the moment the clock ticks past it.
    """

    frame = load_location_forecast(
        warehouse, "hanoi", hours=2, as_of=dt.datetime(2026, 8, 16, 6, 30, tzinfo=UTC)
    )
    hours = [pd.Timestamp(v).tz_convert("UTC").hour for v in frame["valid_at_utc"]]
    assert hours == [5, 6, 7]


def test_the_pinned_window_excludes_other_locations(warehouse: Path) -> None:
    frame = load_location_forecast(
        warehouse, "hanoi", hours=72, as_of=dt.datetime(2026, 8, 16, 6, 0, tzinfo=UTC)
    )
    assert set(frame["location_key"]) == {"hanoi"}


def test_an_anchor_after_the_data_returns_nothing(warehouse: Path) -> None:
    """The state the real warehouse is in between pipeline runs.

    Worth its own test because it is why the fixture pins an anchor at all: read from
    the clock, both sides return nothing and the comparison passes by being empty.
    """

    frame = load_location_forecast(
        warehouse, "hanoi", hours=72, as_of=dt.datetime(2026, 9, 6, 0, 0, tzinfo=UTC)
    )
    assert frame.empty


def test_the_unpinned_window_still_reads_the_clock(warehouse: Path) -> None:
    # The default path must keep working: the app never passes as_of.
    assert load_location_forecast(warehouse, "hanoi", hours=72).empty
