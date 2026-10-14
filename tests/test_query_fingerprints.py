"""Cover the fingerprint canonicalisation, and the pinned window it depends on.

These are not incidental helpers. `scripts/export_query_fingerprints.py` produces the
fixture that the Java repositories are checked against, so a defect in its
canonicalisation does not fail loudly -- it reports a disagreement that is not there,
or hides one that is. Both happened, and both are pinned below.
"""

from __future__ import annotations

import datetime as dt
import json
import types
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from scripts.export_query_fingerprints import (
    FIXTURE_DATABASE,
    NATURAL_KEYS,
    PROJECT_ROOT,
    SNAPSHOT_DIR,
    _check_snapshot_dir,
    build_document,
    canonical,
    fingerprint,
    import_snapshot,
    is_missing,
    load_fixture_database,
    materialize_snapshot,
    read_manifest,
    row_digest,
    row_key,
)
from vn_air_quality_weather.serving_queries import load_location_forecast

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


@pytest.mark.parametrize(
    ("value", "spelled"),
    [
        # The two false reds measured against JDK 25 under the old "%.6f", and their
        # negatives: the shortest repr ends in 5, the exact binary value falls short.
        (1.2345665, "f:1.234566"),
        (-1.2345665, "f:-1.234566"),
        (0.0000005, "f:0.000000"),
        (-0.0000005, "f:0.000000"),
        # Exact binary ties at the sixth place, rounding opposite ways half-to-even.
        (0.0078125, "f:0.007812"),
        (-0.0078125, "f:-0.007812"),
        (0.0234375, "f:0.023438"),
        (-0.0234375, "f:-0.023438"),
        # A zero has one spelling: BigDecimal has no negative zero.
        (0.0000001, "f:0.000000"),
        (-0.0000001, "f:0.000000"),
        (68.7, "f:68.700000"),
        (0.1, "f:0.100000"),
        (31.4, "f:31.400000"),
        (1e15 + 0.5, "f:1000000000000000.500000"),
    ],
)
def test_a_double_is_spelled_as_the_java_side_spells_it(value: float, spelled: str) -> None:
    """The same vectors `QueryFingerprintTests` asserts against `Canonical` in Java."""

    assert canonical(value) == spelled


# --- per-row fingerprints ------------------------------------------------------

HANOI = {
    "location_key": "hanoi",
    "pm25_ugm3": 31.4,
    "lead_hours": 3.0,
    "uv_index": None,
    "is_vintage_aligned": True,
    "valid_at_utc": dt.datetime(2026, 10, 11, 15, 0, tzinfo=UTC),
}
HO_CHI_MINH = {
    "location_key": "ho_chi_minh",
    "pm25_ugm3": 18.25,
    "lead_hours": 3.0,
    "uv_index": 6.5,
    "is_vintage_aligned": False,
    "valid_at_utc": dt.datetime(2026, 10, 11, 22, 0, tzinfo=BANGKOK),
}


def test_the_row_form_matches_the_java_side_byte_for_byte() -> None:
    """The two vectors `QueryFingerprintTests` in vn-air-web asserts as well.

    Either language changing its key or row spelling fails both suites, and neither
    needs a database to notice.
    """

    assert row_key([HANOI["location_key"], HANOI["valid_at_utc"]]) == (
        "s:hanoi|utc:2026-10-11T15:00:00.000000+00:00"
    )
    assert row_digest(HANOI) == "342a002b3530b179"
    assert row_key([HO_CHI_MINH["location_key"], HO_CHI_MINH["valid_at_utc"]]) == (
        "s:ho_chi_minh|utc:2026-10-11T15:00:00.000000+00:00"
    )
    assert row_digest(HO_CHI_MINH) == "59506e8c7380c3a3"


def test_a_value_moved_between_rows_changes_the_rows_and_not_the_columns() -> None:
    """The defect the per-column digest is blind to by construction."""

    key = ("location_key",)
    before = fingerprint(pd.DataFrame([HANOI, HO_CHI_MINH]), key)

    swapped = pd.DataFrame(
        [
            {**HANOI, "pm25_ugm3": HO_CHI_MINH["pm25_ugm3"]},
            {**HO_CHI_MINH, "pm25_ugm3": HANOI["pm25_ugm3"]},
        ]
    )
    after = fingerprint(swapped, key)

    assert after["columns"] == before["columns"]
    changed = {k for k in before["rows"] if before["rows"][k] != after["rows"][k]}
    assert changed == {"s:hanoi", "s:ho_chi_minh"}


def test_a_natural_key_that_repeats_is_refused() -> None:
    # A join that fans a row out would otherwise hide two rows behind one key.
    with pytest.raises(ValueError, match="not unique"):
        fingerprint(pd.DataFrame([HANOI, HANOI]), ("location_key",))


def test_a_natural_key_column_that_is_absent_is_refused() -> None:
    with pytest.raises(ValueError, match="not in the result"):
        fingerprint(pd.DataFrame([HANOI]), ("province_code",))


def test_every_query_declares_a_natural_key() -> None:
    assert set(NATURAL_KEYS) == {
        "provinces",
        "currentConditionsAll",
        "currentConditionsOne",
        "locationForecast",
        "decisionWindows",
        "contiguousWindows",
        "rawSourceHealth",
        "pipelineRuns",
        "aqiHourly",
        "aqiDaily",
        "cityPollutantHourly",
        "coverage",
        "forecastVsAnalysis",
        "modelStationDiscrepancy",
    }


# --- the frozen snapshot -------------------------------------------------------


@pytest.fixture
def clock_warehouse(tmp_path: Path) -> Path:
    """A warehouse with a view over current_timestamp, built the way dbt builds one.

    The view names its source with the warehouse's own catalog, as dbt's compiled SQL
    does, which is why the snapshot must attach the warehouse under its file stem.
    """

    database = tmp_path / "tiny.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("create schema analytics")
        connection.execute(
            "create table analytics.base as "
            "select range as id, cast(range * 1000000000000 as hugeint) as big "
            "from range(50)"
        )
        connection.execute(
            "create view analytics.live as "
            "select id, big, current_timestamp as as_of_utc from tiny.analytics.base"
        )
    return database


def test_a_snapshot_freezes_every_view_at_one_instant(
    clock_warehouse: Path, tmp_path: Path
) -> None:
    """One transaction, so every row of every clock-reading view sees the same now."""

    manifest = materialize_snapshot(clock_warehouse, tmp_path / "snap", ["base", "live"])
    imported = tmp_path / "imported.duckdb"
    import_snapshot(tmp_path / "snap", imported)

    with duckdb.connect(str(imported), read_only=True) as connection:
        stamps = {
            r[0] for r in connection.execute("select as_of_utc from analytics.live").fetchall()
        }
        pinned = pd.Timestamp(manifest["pinnedClockUtc"])
        assert len(stamps) == 1
        assert pd.Timestamp(next(iter(stamps))) == pinned


def test_a_snapshot_keeps_its_types_and_values(clock_warehouse: Path, tmp_path: Path) -> None:
    # HUGEINT is the type that does not survive a careless round trip: Parquet has no
    # 128-bit integer, and a column that came back as DOUBLE would change the SQL Server
    # type the publisher derives from it.
    materialize_snapshot(clock_warehouse, tmp_path / "snap", ["base"])
    imported = tmp_path / "imported.duckdb"
    import_snapshot(tmp_path / "snap", imported)
    with duckdb.connect(str(imported), read_only=True) as connection:
        kind = connection.execute(
            "select data_type from information_schema.columns "
            "where table_name = 'base' and column_name = 'big'"
        ).fetchone()[0]
        total = connection.execute("select sum(big) from analytics.base").fetchone()[0]
    assert kind == "HUGEINT"
    assert total == sum(i * 1_000_000_000_000 for i in range(50))


def test_a_snapshot_carries_no_absolute_path(clock_warehouse: Path, tmp_path: Path) -> None:
    # DuckDB's own load.sql records the exporting machine's paths. It is not kept.
    snapshot = tmp_path / "snap"
    materialize_snapshot(clock_warehouse, snapshot, ["base"])
    assert not (snapshot / "load.sql").exists()
    for text_file in (snapshot / "schema.sql", snapshot / "manifest.json"):
        assert str(tmp_path) not in text_file.read_text(encoding="utf-8")
        assert tmp_path.as_posix() not in text_file.read_text(encoding="utf-8")


def test_an_edited_snapshot_is_refused(clock_warehouse: Path, tmp_path: Path) -> None:
    snapshot = tmp_path / "snap"
    manifest = materialize_snapshot(clock_warehouse, snapshot, ["base"])
    manifest["snapshotId"] = "0" * 64
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SystemExit, match="does not match its manifest"):
        import_snapshot(snapshot, tmp_path / "imported.duckdb")


def test_the_committed_snapshot_fingerprints_the_same_way_twice(tmp_path: Path) -> None:
    """The property the Java fidelity suite depends on: the reference does not move.

    Built from the snapshot committed in data/fixtures, twice, into two scratch
    databases. Equal documents mean the fingerprints depend on the snapshot and on
    nothing else -- not the clock, not the live warehouse, not the order of a set.
    """

    if not (SNAPSHOT_DIR / "manifest.json").exists():
        pytest.fail("data/fixtures/serving_snapshot is missing; it is a committed fixture")

    documents = []
    for run in ("a", "b"):
        database = tmp_path / f"{run}.duckdb"
        manifest = import_snapshot(SNAPSHOT_DIR, database)
        documents.append(build_document(database, manifest))
    assert documents[0] == documents[1]
    assert documents[0]["snapshot"]["id"] == read_manifest(SNAPSHOT_DIR)["snapshotId"]
    assert all(query["rowCount"] > 0 for query in documents[0]["queries"].values())


class _Recorder:
    """Stands in for the publisher and for pyodbc, so no payload reaches a real server."""

    def __init__(self) -> None:
        self.published: list[str] = []
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def publish(self, settings, duckdb_path=None):  # noqa: ANN001 - mirrors the real one
        self.published.append(settings.sqlserver_database)
        return types.SimpleNamespace(total_rows=7)

    def connect(self, connection_string, autocommit=False):  # noqa: ANN001
        recorder = self

        class Cursor:
            def execute(self, sql, *params):  # noqa: ANN001
                recorder.statements.append((sql, params))

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *exc):  # noqa: ANN002
                return False

            def cursor(self):
                return Cursor()

            def commit(self):
                pass

        return Connection()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    import pyodbc

    from vn_air_quality_weather import sqlserver_publish

    fake = _Recorder()
    monkeypatch.setattr(sqlserver_publish, "publish_serving_marts", fake.publish)
    monkeypatch.setattr(pyodbc, "connect", fake.connect)
    # Placeholder credentials, so Settings can assemble a connection string for the fake.
    monkeypatch.setenv("SQLSERVER_USER", "test_user")
    monkeypatch.setenv("SQLSERVER_PASSWORD", "test-only-not-a-secret")
    return fake


MANIFEST = {"snapshotId": "a" * 64, "pinnedClockUtc": "2026-10-11T15:29:04+00:00"}


@pytest.mark.parametrize(
    "target",
    [
        "vn_air_quality",
        "master",
        # Settings splices the name into an ODBC string unquoted: this one passed a
        # blacklist and then connected to the serving database.
        "vn_air_quality_fidelity;DATABASE=vn_air_quality",
        "vn_air_quality_fidelity;",
        # ODBC braces are quoting, not part of the name: {vn_air_quality} is the
        # serving catalog itself.
        "{vn_air_quality}",
        "{VN_AIR_QUALITY}",
        "{vn_air_quality_fidelity}",
        "vn_air_quality_fidelity}",
        "{vn_air_quality_fidelity};DATABASE={vn_air_quality}",
        "[vn_air_quality]",
        " vn_air_quality_fidelity",
        "VN_AIR_QUALITY_FIDELITY",
        "vn_air_quality_fidelity_old",
        "",
    ],
)
def test_the_loader_writes_only_the_one_fixture_database(
    recorder: _Recorder, tmp_path: Path, target: str
) -> None:
    with pytest.raises(SystemExit, match="Refusing to load a fixture into"):
        load_fixture_database(tmp_path / "unused.duckdb", MANIFEST, target)
    assert recorder.published == []
    assert recorder.statements == []


def test_the_loader_publishes_the_fixture_database_and_stamps_it(
    recorder: _Recorder, tmp_path: Path
) -> None:
    rows = load_fixture_database(tmp_path / "unused.duckdb", MANIFEST, FIXTURE_DATABASE)
    assert rows == 7
    assert recorder.published == [FIXTURE_DATABASE]
    inserted = [params for sql, params in recorder.statements if sql.startswith("INSERT")]
    assert inserted and inserted[0][0] == MANIFEST["snapshotId"]


# --- the snapshot directory is ours, and only ours -----------------------------


def test_a_refresh_replaces_its_own_files_and_keeps_anyone_elses(
    clock_warehouse: Path, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snap"
    materialize_snapshot(clock_warehouse, snapshot, ["base", "live"])
    sentinel = snapshot / "README.txt"
    sentinel.write_text("not a snapshot asset", encoding="utf-8")
    (snapshot / "notes").mkdir()

    materialize_snapshot(clock_warehouse, snapshot, ["base"])

    assert sentinel.read_text(encoding="utf-8") == "not a snapshot asset"
    assert (snapshot / "notes").is_dir()
    # The relation dropped from the second refresh is gone; the kept one is current.
    assert not (snapshot / "analytics_live.parquet").exists()
    assert (snapshot / "analytics_base.parquet").exists()


def test_snapshot_shaped_files_without_a_manifest_are_not_touched(
    clock_warehouse: Path, tmp_path: Path
) -> None:
    target = tmp_path / "somebody_elses"
    target.mkdir()
    foreign = target / "analytics_results.parquet"
    foreign.write_bytes(b"not ours")
    with pytest.raises(SystemExit, match="without a valid manifest"):
        materialize_snapshot(clock_warehouse, target, ["base"])
    assert foreign.read_bytes() == b"not ours"


def test_an_invalid_manifest_is_not_trusted(clock_warehouse: Path, tmp_path: Path) -> None:
    target = tmp_path / "snap"
    target.mkdir()
    (target / "manifest.json").write_text('{"snapshotId": "short"}', encoding="utf-8")
    (target / "schema.sql").write_text("-- somebody's", encoding="utf-8")
    with pytest.raises(SystemExit, match="without a valid manifest"):
        materialize_snapshot(clock_warehouse, target, ["base"])
    assert (target / "schema.sql").read_text(encoding="utf-8") == "-- somebody's"


@pytest.mark.parametrize(
    "dangerous",
    [
        lambda tmp: Path(tmp.anchor),
        lambda tmp: Path.home(),
        lambda tmp: PROJECT_ROOT,
        lambda tmp: PROJECT_ROOT / "data",
        lambda tmp: PROJECT_ROOT / "data" / "fixtures",
        lambda tmp: PROJECT_ROOT.parent,
    ],
    ids=["filesystem-root", "home", "project-root", "data", "data-fixtures", "above-project"],
)
def test_a_shared_directory_is_refused(tmp_path: Path, dangerous) -> None:  # noqa: ANN001
    # The check itself, not materialize_snapshot: were the guard ever to regress, a test
    # driving the full path would write into the home directory or the repository root.
    # That materialize_snapshot runs this check before touching anything is covered by
    # the foreign-files test above, which goes through the full path in tmp_path.
    with pytest.raises(SystemExit, match="shared directory"):
        _check_snapshot_dir(dangerous(tmp_path))


def test_a_dedicated_directory_is_accepted(tmp_path: Path) -> None:
    assert _check_snapshot_dir(tmp_path / "new_snapshot") == []
    assert _check_snapshot_dir(SNAPSHOT_DIR)  # the committed one, holding its own assets
