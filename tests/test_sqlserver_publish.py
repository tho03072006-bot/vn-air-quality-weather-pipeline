"""Unit tests for the DuckDB to SQL Server publish path.

Everything here is pure: the quoting, the type resolution, the value guards and the
content hash. The parts that need a live SQL Server are covered by
`scripts/verify_sqlserver_publish.py`, which compares the two databases against each
other rather than against a mock.

The module under test lives in the installed package, not in `scripts/`: the Airflow
image installs the package only, so the DAG task and the command line share one
implementation instead of two.
"""

from __future__ import annotations

import datetime as dt
import math

import pytest

from vn_air_quality_weather.settings import Settings
from vn_air_quality_weather.sqlserver_publish import (
    CLOCK_DERIVED_COLUMNS,
    Column,
    Relation,
    TablePlan,
    canonical,
    coerce_row,
    content_hash,
    create_index_sql,
    create_table_sql,
    duck,
    mssql,
    resolve_varchar_type,
    to_parameter,
)


def plan(rows: tuple[tuple[object, ...], ...]) -> TablePlan:
    columns = (
        Column("location_key", "VARCHAR", "nvarchar(128)"),
        Column("pm25_ugm3", "DOUBLE", "float(53)"),
        Column("as_of_utc", "TIMESTAMP WITH TIME ZONE", "datetimeoffset(6)"),
    )
    return TablePlan(Relation("mart_probe", ("location_key",)), columns, rows)


# --- quoting -----------------------------------------------------------------


def test_duckdb_quoting_uses_double_quotes_not_brackets() -> None:
    """Regression: `[col]` is list construction in DuckDB, not a quoted identifier.

    Written as its own test because the bug it guards produced no error at all. Every
    scalar came back wrapped in a one-element list and every measured string width
    came back as 1, and the publish would have written that shape into SQL Server if
    a type guard had not happened to reject it.
    """

    assert duck("province_code") == '"province_code"'
    assert "[" not in duck("province_code")


def test_sqlserver_quoting_uses_brackets() -> None:
    assert mssql("province_code") == "[province_code]"


@pytest.mark.parametrize("name", ["drop table", "a-b", "1col", "col]", 'col"', ""])
def test_unsafe_identifiers_are_refused(name: str) -> None:
    with pytest.raises(ValueError):
        duck(name)
    with pytest.raises(ValueError):
        mssql(name)


# --- type resolution ---------------------------------------------------------


def test_short_columns_stay_indexable() -> None:
    assert resolve_varchar_type(11, None) == "nvarchar(128)"


def test_long_columns_widen_beyond_the_index_key_limit() -> None:
    assert resolve_varchar_type(73, None) == "nvarchar(4000)"


def test_empty_relation_borrows_the_width_measured_elsewhere() -> None:
    """An empty mart's city_key should not widen just because it has no rows yet."""

    assert resolve_varchar_type(None, 11) == "nvarchar(128)"


def test_unmeasurable_column_widens_rather_than_guessing_narrow() -> None:
    assert resolve_varchar_type(None, None) == "nvarchar(4000)"


# --- value guards ------------------------------------------------------------


def test_none_survives_as_none() -> None:
    columns = [Column("pm25_ugm3", "DOUBLE", "float(53)")]
    assert coerce_row((None,), columns) == (None,)


def test_composite_values_are_refused() -> None:
    columns = [Column("province_code", "VARCHAR", "nvarchar(128)")]
    with pytest.raises(ValueError, match="scalar VARCHAR was expected"):
        coerce_row((["01"],), columns)


def test_nan_is_refused_rather_than_written_as_null() -> None:
    columns = [Column("pm25_ugm3", "DOUBLE", "float(53)")]
    with pytest.raises(ValueError, match="no NaN"):
        coerce_row((math.nan,), columns)
    with pytest.raises(ValueError, match="no NaN"):
        coerce_row((math.inf,), columns)


def test_aware_datetimes_are_stored_as_utc() -> None:
    """Regression: an offset that survives one binding path and not the other.

    pyodbc keeps the offset on the fast_executemany path and drops it on the plain
    one, binding the wall clock as if it were UTC. That put a vintage seven hours
    out in the control row while the same instant crossed correctly in the marts.
    Normalising here makes the stored value independent of the driver's choice, and
    matches the project's rule that storage is UTC.
    """

    bangkok = dt.timezone(dt.timedelta(hours=7))
    columns = [Column("forecast_issued_at_utc", "TIMESTAMP WITH TIME ZONE", "datetimeoffset(6)")]
    (stored,) = coerce_row((dt.datetime(2026, 8, 16, 13, 0, tzinfo=bangkok),), columns)
    assert stored == dt.datetime(2026, 8, 16, 6, 0, tzinfo=dt.UTC)
    assert stored.utcoffset() == dt.timedelta(0)


def test_aware_datetimes_bind_as_text_with_an_explicit_offset() -> None:
    """Regression: pyodbc's fast path stamps the client's offset over the value's own.

    Measured, not assumed: with fast_executemany on, 06:00 UTC was stored as
    06:00+07:00 -- seven hours out, no error raised. Binding ISO-8601 text makes the
    stored instant the same whichever path the driver takes.
    """

    utc = dt.datetime(2026, 8, 16, 6, 0, 4, 714908, tzinfo=dt.UTC)
    assert to_parameter(utc) == "2026-08-16 06:00:04.714908+00:00"


def test_the_bound_text_is_fixed_width_even_on_a_whole_second() -> None:
    """A variable-width stamp would sort and compare wrongly as text."""

    assert to_parameter(dt.datetime(2026, 8, 16, 6, 0, tzinfo=dt.UTC)).endswith(
        "06:00:00.000000+00:00"
    )


def test_values_that_are_not_aware_datetimes_pass_through_untouched() -> None:
    assert to_parameter(13.7) == 13.7
    assert to_parameter(None) is None
    assert to_parameter("hanoi") == "hanoi"
    naive = dt.datetime(2026, 8, 19, 12, 0)
    assert to_parameter(naive) is naive


def test_naive_timestamps_are_left_alone() -> None:
    """The _local columns carry wall-clock time with no zone and must not shift."""

    columns = [Column("valid_at_local", "TIMESTAMP", "datetime2(6)")]
    naive = dt.datetime(2026, 8, 19, 12, 0)
    assert coerce_row((naive,), columns) == (naive,)


def test_hugeint_beyond_bigint_is_refused() -> None:
    columns = [Column("paired_hours", "HUGEINT", "bigint")]
    assert coerce_row((275,), columns) == (275,)
    with pytest.raises(ValueError, match="bigint"):
        coerce_row((2**63,), columns)


# --- canonical form ----------------------------------------------------------


def test_same_instant_in_two_offsets_is_one_canonical_value() -> None:
    seven = dt.timezone(dt.timedelta(hours=7))
    assert canonical(dt.datetime(2026, 8, 16, 13, 0, tzinfo=seven)) == canonical(
        dt.datetime(2026, 8, 16, 6, 0, tzinfo=dt.UTC)
    )


def test_naive_and_aware_are_never_confused() -> None:
    assert canonical(dt.datetime(2026, 8, 16, 6, 0)) != canonical(
        dt.datetime(2026, 8, 16, 6, 0, tzinfo=dt.UTC)
    )


def test_floats_round_trip_exactly() -> None:
    assert canonical(0.1 + 0.2) != canonical(0.3)


def test_booleans_are_not_integers() -> None:
    assert canonical(True) != canonical(1)


# --- content hash ------------------------------------------------------------


def test_clock_columns_do_not_change_the_content_hash() -> None:
    """A publish that only re-read the clock must not look like new data.

    This is what stops a five-minute publish timer from waking every open browser
    every five minutes.
    """

    first = dt.datetime(2026, 9, 5, 16, 3, tzinfo=dt.UTC)
    second = dt.datetime(2026, 9, 5, 16, 8, tzinfo=dt.UTC)
    assert content_hash([plan((("hanoi", 13.7, first),))]) == content_hash(
        [plan((("hanoi", 13.7, second),))]
    )


def test_a_real_reading_changes_the_content_hash() -> None:
    stamp = dt.datetime(2026, 9, 5, 16, 3, tzinfo=dt.UTC)
    assert content_hash([plan((("hanoi", 13.7, stamp),))]) != content_hash(
        [plan((("hanoi", 13.8, stamp),))]
    )


def test_row_order_does_not_change_the_content_hash() -> None:
    stamp = dt.datetime(2026, 9, 5, 16, 3, tzinfo=dt.UTC)
    forward = plan((("hanoi", 13.7, stamp), ("hue", 8.3, stamp)))
    backward = plan((("hue", 8.3, stamp), ("hanoi", 13.7, stamp)))
    assert content_hash([forward]) == content_hash([backward])


def test_freshness_columns_are_all_declared_clock_derived() -> None:
    """The four columns mart_current_conditions derives from its own clock reading."""

    assert {
        "as_of_utc",
        "data_age_minutes",
        "forecast_age_minutes",
        "freshness_status",
        "is_forecast_horizon_exhausted",
    } == set(CLOCK_DERIVED_COLUMNS)


# --- generated DDL -----------------------------------------------------------


def test_create_table_names_every_column_and_allows_nulls() -> None:
    sql = create_table_sql(plan(()))
    assert "[dbo].[mart_probe]" in sql
    assert "[pm25_ugm3] float(53) NULL" in sql
    assert "[as_of_utc] datetimeoffset(6) NULL" in sql


def test_create_index_uses_the_declared_columns() -> None:
    assert create_index_sql(plan(())) == (
        "CREATE INDEX [IX_mart_probe] ON [dbo].[mart_probe] ([location_key])"
    )


def test_unique_relations_get_a_unique_index() -> None:
    unique = TablePlan(
        Relation("dim_probe", ("province_code",), unique=True),
        (Column("province_code", "VARCHAR", "nvarchar(128)"),),
        (),
    )
    assert create_index_sql(unique).startswith("CREATE UNIQUE INDEX")


# --- connection string -------------------------------------------------------


def base_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "sqlserver_host": "localhost",
        "sqlserver_port": 1433,
        "sqlserver_database": "vn_air_quality",
        "sqlserver_user": "vn_air_web",
        "sqlserver_password": "s3cret",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_connection_string_matches_the_driver_18_shape() -> None:
    odbc = base_settings().require_sqlserver_odbc()
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in odbc
    assert "SERVER=tcp:localhost,1433" in odbc
    assert "DATABASE=vn_air_quality" in odbc
    assert "Encrypt=yes" in odbc
    assert "TrustServerCertificate=yes" in odbc


def test_a_brace_in_the_password_is_escaped_not_left_to_end_the_value() -> None:
    """Generated passwords contain punctuation, and ODBC ends a braced value at `}`."""

    odbc = base_settings(sqlserver_password="pa}ss").require_sqlserver_odbc()
    assert "PWD={pa}}ss}" in odbc


def test_encryption_can_be_turned_off_for_a_server_that_is_not_localhost() -> None:
    odbc = base_settings(
        sqlserver_encrypt=False, sqlserver_trust_server_certificate=False
    ).require_sqlserver_odbc()
    assert "Encrypt=no" in odbc
    assert "TrustServerCertificate=no" in odbc


@pytest.mark.parametrize("missing", ["sqlserver_user", "sqlserver_password"])
def test_missing_credentials_fail_without_printing_the_secret(missing: str) -> None:
    settings = base_settings(**{missing: ""})
    with pytest.raises(ValueError) as error:
        settings.require_sqlserver_odbc()
    assert "s3cret" not in str(error.value)
    assert missing.upper() in str(error.value)
