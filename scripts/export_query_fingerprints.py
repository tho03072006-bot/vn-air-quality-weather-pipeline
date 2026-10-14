"""Fingerprint every serving query over one frozen snapshot, for the Java translation.

The web application restates the sixteen queries in
`vn_air_quality_weather/serving_queries.py` in T-SQL. A restatement can be wrong in ways
that compile, connect and return rows: a predicate dropped, an ordering lost, two
columns swapped between fields of the same type, two rows' values exchanged. None of
those raise anything.

**One snapshot, one clock, both sides.** The comparison used to run the Python queries
against the live warehouse and the Java ones against the live SQL Server copy. Five of
the serving relations are views over `current_timestamp`, and the copy freezes them at
publish time, so the two sides read two different "nows". With the publish timer
rewriting the copy every five minutes the fingerprints went stale within the hour:
measured on 2026-10-11, the suite was 61/61 at 14:29 UTC and two tests red at 14:38
with nothing changed in either codebase. A fixture that has to be regenerated every five
minutes is not a fixture.

So the reference is a snapshot, not a database:

1. `--refresh-snapshot` materialises the fifteen serving relations from the real
   warehouse **inside one transaction**, so every view reads the same
   `current_timestamp`, and writes them to `data/fixtures/serving_snapshot/` as Parquet
   plus the exact DDL. That instant is recorded as the pinned clock. Run it on purpose,
   when the data the suite is checked against should move; nothing else does.
2. By default the script imports that committed snapshot into a scratch DuckDB, runs
   every Python query against it -- the forecast window anchored on the pinned clock --
   and writes the fingerprints. The output depends only on the snapshot, so running it
   twice writes the same file.
3. `--load-fixture-db` publishes the same snapshot into an isolated SQL Server database
   with the real publisher, so the Java repositories read it through the column types
   production uses, and stamps it with the snapshot id the Java test checks before it
   compares anything. It refuses to write the serving database.

**What is fingerprinted.** Per query: the row count; per column, the non-null count
and a digest over its sorted values, so a failure names the column; and per row, a
digest keyed by the query's natural key. The per-column digest is blind to a value
moving between rows -- swap Hanoi's PM2.5 with Ho Chi Minh City's and every column
still holds the same multiset -- and the per-row digest is what catches it, by name.

Clock-derived columns (`as_of_utc`, the ages, the exhaustion flag, the freshness label)
used to be excluded because they moved between two reads. In a snapshot they do not
move, so they are compared like every other column.

**Values are canonicalised, not formatted.** Floats go through `%.6f` rather than
`repr`: both languages round the same binary double to the same six decimals, while
their shortest-round-trip spellings differ (`1e-05` against `1.0E-5`). Timestamps
become UTC instants, so two spellings of one moment are one string.

    OUT=../vn-air-web/src/test/resources/query_fingerprints.json
    python scripts/export_query_fingerprints.py --out $OUT
    python scripts/export_query_fingerprints.py --load-fixture-db --out $OUT
    python scripts/export_query_fingerprints.py --refresh-snapshot --load-fixture-db --out $OUT
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from vn_air_quality_weather import serving_queries as data_access
from vn_air_quality_weather.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUT = PROJECT_ROOT / "data" / "fixtures" / "query_fingerprints.json"
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "fixtures" / "serving_snapshot"
MANIFEST = "manifest.json"
SCHEMA = "schema.sql"

# The isolated database the Java fidelity suite reads. Never the serving database:
# `load_fixture_database` accepts this exact name and nothing else.
FIXTURE_DATABASE = "vn_air_quality_fidelity"
SERVING_DATABASE = "vn_air_quality"

# The natural key of each query's result. Every key is checked for uniqueness when the
# fingerprint is built, so a join that fans out a row fails here, by name, rather than
# hiding inside a digest.
NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "provinces": ("province_code",),
    "currentConditionsAll": ("location_key",),
    "currentConditionsOne": ("location_key",),
    "locationForecast": ("location_key", "valid_at_utc"),
    "decisionWindows": ("location_key", "valid_at_utc"),
    "contiguousWindows": ("location_key", "duration_hours", "window_start_utc"),
    "rawSourceHealth": ("source",),
    "pipelineRuns": ("run_id", "data_date_utc"),
    "aqiHourly": ("city_key", "observed_at_utc"),
    "aqiDaily": ("city_key", "data_date_vn"),
    "cityPollutantHourly": ("city_key", "observed_at_utc", "pollutant", "source_type"),
    "coverage": ("city_key", "data_date_utc", "pollutant", "source_type", "source_name"),
    "forecastVsAnalysis": ("location_key", "pollutant", "lead_band"),
    "modelStationDiscrepancy": ("location_key", "pollutant", "lead_band"),
}

# Separators that cannot occur inside a canonical value, so ("ab", "c") and ("a", "bc")
# never collapse into one string. The Java side uses the same three.
RECORD_SEPARATOR = "\x1e"
UNIT_SEPARATOR = "\x1f"
KEY_SEPARATOR = "|"

ROW_DIGEST_HEX = 16


def is_missing(value: Any) -> bool:
    """True for every way a missing value can arrive from a DataFrame.

    There are four, and they are not interchangeable. `None` and `float('nan')` are the
    obvious two; `pd.NaT` stands in a datetime column; and `pd.NA` stands in a *nullable*
    dtype, which is what DuckDB produces for an INTEGER column that has nulls. Missing
    that last one is not a rounding error in the fingerprint -- `pd.NA` fell through to
    the string branch and was recorded as the literal `s:<NA>`, so three absent AQI
    sub-indices were counted as three present ones, and the Java side that reported them
    correctly as null was the side that looked wrong.

    `pd.isna` covers all four, and raises on a container rather than answering for it,
    so the call is guarded rather than trusted.
    """

    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


SIX_PLACES = Decimal("0.000001")


def six_decimals(value: float) -> str:
    """A double to six decimals, rounded the way the Java side rounds it.

    Both sides take the double's **exact** binary value -- `Decimal.from_float` here,
    `new BigDecimal(double)` in Java -- and round it half-to-even. Formatting with
    `%.6f` looked equivalent and is not: Python's rounds the exact value, while Java's
    `String.format` rounds the shortest decimal that round-trips, half-up. They disagree
    exactly where that shortest decimal ends in a 5 the exact value does not: 1.2345665
    is stored as 1.23456649999..., which Python wrote as 1.234566 and Java as 1.234567,
    and 0.0000005 came out as 0.000000 against 0.000001. Both were measured; both were
    false reds with nothing wrong in either query.

    Zero has one spelling. A small negative rounds to a signed zero in Decimal and to an
    unsigned one in BigDecimal, so the sign of a zero is dropped here.
    """

    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    rounded = Decimal.from_float(value).quantize(SIX_PLACES, rounding=ROUND_HALF_EVEN)
    if rounded.is_zero():
        rounded = abs(rounded)
    return format(rounded, "f")


def canonical(value: Any) -> str:
    """One spelling per value, identical in Python and in Java."""

    if is_missing(value):
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (pd.Timestamp, dt.datetime)):
        stamp = pd.Timestamp(value)
        if stamp.tz is None:
            return "naive:" + stamp.isoformat(timespec="microseconds")
        return "utc:" + stamp.tz_convert("UTC").isoformat(timespec="microseconds")
    if isinstance(value, dt.date):
        return "date:" + value.isoformat()
    if isinstance(value, float):
        # An integral double is spelled as an integer, on both sides, because pandas
        # has already destroyed the distinction. DuckDB's HUGEINT is 128 bits, NumPy
        # has no such type, and a HUGEINT column therefore arrives as float64 --
        # `paired_hours` is 275 in the warehouse and 275.0 here. Recording it as
        # `f:275.000000` would disagree with the Java side's `i:275` for a difference
        # that exists only in the container. Truncation is still caught: a Java field
        # that read 68.7 as an int would render `i:68` against this `f:68.700000`.
        if value.is_integer() and abs(value) < 2**53:
            return f"i:{int(value)}"
        return "f:" + six_decimals(value)
    if isinstance(value, (int,)):
        return f"i:{value}"
    return f"s:{value}"


# --- fingerprints -----------------------------------------------------------------


def row_key(values: Iterable[Any]) -> str:
    """The natural key of one row, spelled canonically."""

    return KEY_SEPARATOR.join(canonical(v) for v in values)


def row_digest(record: dict[str, Any]) -> str:
    """A digest over every column of one row, in column-name order.

    Name-ordered and name-labelled, so the digest is independent of the order a query
    lists its columns in and two columns exchanging values changes it.
    """

    body = UNIT_SEPARATOR.join(f"{name}={canonical(record[name])}" for name in sorted(record))
    return hashlib.sha256(body.encode()).hexdigest()[:ROW_DIGEST_HEX]


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    # Built from per-column tolist(), the same boxing the column digests use, so a value
    # cannot be spelled one way in its column and another in its row.
    columns = {str(name): frame[name].tolist() for name in frame.columns}
    return [{name: values[i] for name, values in columns.items()} for i in range(len(frame))]


def fingerprint(frame: pd.DataFrame, key: Sequence[str] | None = None) -> dict[str, Any]:
    """Reduce one result set to a shape both languages can compute and compare."""

    columns: dict[str, Any] = {}
    for name in frame.columns:
        rendered = sorted(canonical(v) for v in frame[name].tolist())
        digest = hashlib.sha256(RECORD_SEPARATOR.join(rendered).encode()).hexdigest()
        columns[str(name)] = {
            "nonNull": int(sum(1 for r in rendered if r != "NULL")),
            "digest": digest,
        }
    result: dict[str, Any] = {"rowCount": int(len(frame)), "columns": columns}

    if key is not None:
        absent = [name for name in key if name not in frame.columns]
        if absent:
            raise ValueError(f"natural key columns {absent} are not in the result")
        rows: dict[str, str] = {}
        for record in _records(frame):
            identity = row_key(record[name] for name in key)
            if identity in rows:
                raise ValueError(
                    f"natural key {tuple(key)} is not unique: {identity} appears twice. "
                    "Either the key is wrong or the query fans out a row."
                )
            rows[identity] = row_digest(record)
        result["naturalKey"] = list(key)
        result["rows"] = dict(sorted(rows.items()))
    return result


# --- the snapshot -----------------------------------------------------------------


def snapshot_id(connection: duckdb.DuckDBPyConnection) -> str:
    """A content digest over every row of every serving table, independent of file bytes.

    Computed from DuckDB's own text for each row, not from `canonical`. The identity of
    the data must not move when the comparison's spelling rules do: the first version
    hashed canonical values, so correcting how a double is rounded would have invalidated
    every committed snapshot without a single row changing. DuckDB's spelling depends
    only on the value and the DuckDB version, and a version change failing this check
    loudly is the right outcome.
    """

    tables = [
        r[0]
        for r in connection.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'analytics' order by table_name"
        ).fetchall()
    ]
    digest = hashlib.sha256()
    for table in tables:
        rows = sorted(
            str(r[0])
            for r in connection.execute(
                f'select cast(t as varchar) from analytics."{table}" as t'
            ).fetchall()
        )
        digest.update(table.encode())
        digest.update(RECORD_SEPARATOR.join(rows).encode())
    return digest.hexdigest()


# The only files a snapshot directory is allowed to contain that this script will
# replace. Anything else in the directory is somebody else's and is left alone.
SNAPSHOT_ASSET = re.compile(r"^(manifest\.json|schema\.sql|analytics_[A-Za-z0-9_]+\.parquet)$")


def _check_snapshot_dir(snapshot_dir: Path) -> list[Path]:
    """Refuse a dangerous target, and return the previous snapshot's files to replace.

    The first version emptied every file in whatever `--snapshot-dir` named, so pointing
    it at the repository or `data/` by mistake would have deleted unrelated files. Now:

    * a filesystem root, the home directory, the project root, `data/`, `data/fixtures/`
      or any directory above them is refused outright;
    * inside the target, only files whose names a snapshot writes are candidates;
    * and those are only replaced when the directory already holds a valid manifest. A
      directory with snapshot-shaped files but no manifest is not provably ours, so it
      is refused rather than guessed about.

    Called before the warehouse is opened, so a refusal has changed nothing.
    """

    resolved = snapshot_dir.resolve()
    protected = {
        PROJECT_ROOT.resolve(),
        (PROJECT_ROOT / "data").resolve(),
        (PROJECT_ROOT / "data" / "fixtures").resolve(),
        Path.home().resolve(),
    }
    is_root = resolved == Path(resolved.anchor)
    is_protected = resolved in protected or any(resolved in p.parents for p in protected)
    if is_root or is_protected:
        raise SystemExit(
            f"Refusing to write a snapshot into {resolved}: it is a shared directory, not a "
            "snapshot directory. Use a dedicated one such as data/fixtures/serving_snapshot."
        )
    if resolved.exists() and not resolved.is_dir():
        raise SystemExit(f"Refusing to write a snapshot over the file {resolved}.")
    if not resolved.exists():
        return []

    assets = [p for p in resolved.iterdir() if p.is_file() and SNAPSHOT_ASSET.match(p.name)]
    if not assets:
        return []
    manifest = resolved / MANIFEST
    try:
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        valid = isinstance(recorded, dict) and re.fullmatch(
            r"[0-9a-f]{64}", str(recorded.get("snapshotId", ""))
        )
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise SystemExit(
            f"{resolved} holds snapshot-shaped files without a valid manifest. Refusing to "
            "replace files this script cannot prove it wrote."
        )
    return assets


def materialize_snapshot(
    warehouse: Path, snapshot_dir: Path, relations: Sequence[str] | None = None
) -> dict[str, Any]:
    """Freeze the serving relations of a real warehouse at one instant.

    One transaction, because DuckDB's `current_timestamp` is the transaction's start
    time: every clock-reading view materialised inside it sees the same "now", which is
    then the pinned clock for both sides of the comparison.

    The warehouse is attached under its own file stem. The stored view definitions
    carry that catalog name, so attached under any other alias they do not resolve.
    """

    previous = _check_snapshot_dir(snapshot_dir)

    if relations is None:
        # Imported here so the publisher's relation list stays the single definition of
        # what is served, without this module depending on pyodbc to fingerprint.
        from vn_air_quality_weather.sqlserver_publish import SERVING_RELATIONS

        relations = [relation.name for relation in SERVING_RELATIONS]

    catalog = warehouse.stem
    with tempfile.TemporaryDirectory() as scratch:
        scratch_path = Path(scratch)
        database = scratch_path / "snapshot.duckdb"
        with duckdb.connect(str(database)) as connection:
            connection.execute(f"ATTACH '{warehouse.as_posix()}' AS \"{catalog}\" (READ_ONLY)")
            connection.execute("CREATE SCHEMA analytics")
            connection.execute("BEGIN TRANSACTION")
            pinned = connection.execute("select current_timestamp").fetchone()[0]
            for name in relations:
                connection.execute(
                    f'CREATE TABLE analytics."{name}" AS '
                    f'SELECT * FROM "{catalog}".analytics."{name}"'
                )
            connection.execute("COMMIT")
            connection.execute(f'DETACH "{catalog}"')

            counts = {
                name: int(
                    connection.execute(f'select count(*) from analytics."{name}"').fetchone()[0]
                )
                for name in relations
            }
            identity = snapshot_id(connection)
            export = scratch_path / "export"
            connection.execute(
                f"EXPORT DATABASE '{export.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )

        # load.sql is not kept: it records absolute paths from the machine that wrote
        # it, which would make the fixture unportable and leak a home directory into
        # the repository. `import_snapshot` loads the Parquet files itself.
        for stale in previous:
            stale.unlink()
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(export / SCHEMA, snapshot_dir / SCHEMA)
        for parquet in sorted(export.glob("*.parquet")):
            shutil.copy2(parquet, snapshot_dir / parquet.name)

    manifest = {
        "snapshotId": identity,
        "pinnedClockUtc": pd.Timestamp(pinned).tz_convert("UTC").isoformat(),
        "source": warehouse.name,
        "relations": counts,
    }
    (snapshot_dir / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def read_manifest(snapshot_dir: Path) -> dict[str, Any]:
    path = snapshot_dir / MANIFEST
    if not path.exists():
        raise SystemExit(
            f"No snapshot at {snapshot_dir}. Create one from the real warehouse with "
            "--refresh-snapshot."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def import_snapshot(snapshot_dir: Path, database: Path) -> dict[str, Any]:
    """Rebuild the snapshot in a fresh DuckDB file and prove it is the one recorded."""

    manifest = read_manifest(snapshot_dir)
    with duckdb.connect(str(database)) as connection:
        connection.execute((snapshot_dir / SCHEMA).read_text(encoding="utf-8"))
        tables = [
            r[0]
            for r in connection.execute(
                "select table_name from information_schema.tables "
                "where table_schema = 'analytics' order by table_name"
            ).fetchall()
        ]
        for table in tables:
            parquet = snapshot_dir / f"analytics_{table}.parquet"
            if not parquet.exists():
                raise SystemExit(f"Snapshot is incomplete: {parquet.name} is missing")
            connection.execute(
                f'INSERT INTO analytics."{table}" BY NAME SELECT * FROM read_parquet(?)',
                [parquet.as_posix()],
            )
        # The id is recomputed from the rows, not trusted from the manifest. A Parquet
        # file edited by hand, or a type that does not survive the round trip, shows
        # up here instead of as a mysterious mismatch on the Java side.
        recomputed = snapshot_id(connection)
    if recomputed != manifest["snapshotId"]:
        raise SystemExit(
            f"Snapshot content does not match its manifest: {recomputed} != "
            f"{manifest['snapshotId']}. Refresh it rather than editing it."
        )
    return manifest


# --- the queries ------------------------------------------------------------------

# `serving_queries.load_pipeline_health` aggregates the raw schema: four counts over a
# quarter of a million rows. Neither the serving database nor this snapshot carries raw
# tables, and neither should, so the aggregate was moved into dbt as
# `mart_raw_source_health` -- the same four selects, the same labels -- and the Java side
# reads that. In a snapshot the mart is therefore the reference for this query. The
# substitution is only honest while the two agree, so `--refresh-snapshot` checks that
# they do on the real warehouse before it freezes anything.
RAW_SOURCE_HEALTH_FROM_MART = """
    select source, row_count, latest_fetch_utc
    from analytics.mart_raw_source_health
    order by source
"""


def load_raw_source_health_from_mart(database: Path) -> pd.DataFrame:
    with duckdb.connect(str(database), read_only=True) as connection:
        return connection.execute(RAW_SOURCE_HEALTH_FROM_MART).fetchdf()


def assert_raw_source_health_mart_matches(warehouse: Path) -> None:
    """Refuse to freeze a snapshot whose raw-source mart disagrees with the raw schema."""

    from_raw = fingerprint(data_access.load_pipeline_health(warehouse), ("source",))
    from_mart = fingerprint(load_raw_source_health_from_mart(warehouse), ("source",))
    if from_raw != from_mart:
        raise SystemExit(
            "mart_raw_source_health no longer matches the raw schema it summarises -- "
            "something was ingested after the last dbt build. Run dbt build, then refresh."
        )


def build_document(database: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Run every serving query against an imported snapshot and fingerprint the results."""

    path = database
    options = data_access.load_filter_options(path)
    cities = sorted(str(c) for c in options["cities"])
    pollutant = "pm25" if "pm25" in options["pollutants"] else str(options["pollutants"][0])
    source_type = (
        "MODELED" if "MODELED" in options["source_types"] else str(options["source_types"][0])
    )
    start = options["min_date"]
    end = options["max_date"]

    provinces = data_access.load_provinces(path)
    location_key = str(provinces.iloc[0]["province_key"])
    for candidate in provinces.itertuples():
        if candidate.province_key == "hanoi":
            location_key = "hanoi"
            break

    # The forecast window is anchored on the snapshot's own clock: the instant at which
    # its views were materialised, so the window is the one a reader saw at that moment.
    pinned = pd.Timestamp(manifest["pinnedClockUtc"]).tz_convert("UTC")

    results: dict[str, pd.DataFrame] = {
        "provinces": provinces,
        "currentConditionsAll": data_access.load_current_conditions(path),
        "currentConditionsOne": data_access.load_current_conditions(path, location_key),
        "locationForecast": data_access.load_location_forecast(
            path, location_key, hours=72, as_of=pinned.to_pydatetime()
        ),
        "decisionWindows": data_access.load_decision_windows(path, location_key, limit=5),
        "contiguousWindows": data_access.load_contiguous_windows(
            path, location_key, limit_per_duration=5
        ),
        "rawSourceHealth": load_raw_source_health_from_mart(path),
        "pipelineRuns": data_access.load_pipeline_runs(path, limit=20),
        "aqiHourly": data_access.load_aqi_hourly(path, start, end, tuple(cities), source_type),
        "aqiDaily": data_access.load_aqi_daily(path, start, end, tuple(cities), source_type),
        "cityPollutantHourly": data_access.load_hourly_mart(
            path, start, end, tuple(cities), pollutant, source_type
        ),
        "coverage": data_access.load_coverage(
            path, start, end, tuple(cities), pollutant, source_type
        ),
        "forecastVsAnalysis": data_access.load_forecast_vs_analysis(path),
        "modelStationDiscrepancy": data_access.load_model_station_discrepancy(path),
    }

    # A comparison over zero rows passes by being empty, which is the failure the old
    # live-warehouse fixture fell into once its horizon elapsed. Refuse to write one.
    empty = [name for name, frame in results.items() if frame.empty]
    if empty:
        raise SystemExit(
            f"These queries return no rows on this snapshot: {', '.join(empty)}. A "
            "fingerprint over nothing proves nothing; refresh the snapshot from a warehouse "
            "whose forecast horizon has not elapsed."
        )

    queries = {name: fingerprint(frame, NATURAL_KEYS[name]) for name, frame in results.items()}

    return {
        "snapshot": {
            "id": manifest["snapshotId"],
            "pinnedClockUtc": manifest["pinnedClockUtc"],
            "source": manifest["source"],
        },
        "fixtureDatabase": FIXTURE_DATABASE,
        "parameters": {
            "locationKey": location_key,
            "cities": cities,
            "pollutant": pollutant,
            "sourceType": source_type,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "forecastAsOfUtc": pinned.isoformat(),
            "forecastLeadHours": 72,
            "decisionWindowLimit": 5,
            "contiguousWindowMaxRank": 5,
            "pipelineRunLimit": 20,
        },
        "filterOptions": {
            "minDate": start.isoformat(),
            "maxDate": end.isoformat(),
            "cities": cities,
            "pollutants": sorted(str(p) for p in options["pollutants"]),
            "sourceTypes": sorted(str(s) for s in options["source_types"]),
        },
        "queries": queries,
    }


# --- the isolated SQL Server copy -------------------------------------------------


def load_fixture_database(database: Path, manifest: dict[str, Any], target: str) -> int:
    """Publish the snapshot into an isolated SQL Server database and stamp its identity.

    The real publisher does the work, so the Java side reads the column types and
    representations production uses rather than a hand-written approximation of them.
    The stamp is a separate one-row table the Java test reads before comparing: a
    fixture database loaded from a different snapshot fails on that line, with the
    command that fixes it, instead of as a column digest that disagrees.
    """

    # An allow-list of exactly one name, compared exactly. The first version refused
    # only "vn_air_quality", which let `master` through, and let
    # `vn_air_quality_fidelity;DATABASE=vn_air_quality` through as well: Settings splices
    # the database name into an ODBC connection string unquoted, so a name carrying a
    # delimiter can redirect the connection to the serving database while every check on
    # the name itself passes. No normalisation either -- a trimmed or case-folded
    # comparison is a second spelling of the same name that this function would have to
    # reason about. Checked before pyodbc or the publisher is even imported.
    if target != FIXTURE_DATABASE:
        raise SystemExit(
            f"Refusing to load a fixture into {target!r}. The only database this writes is "
            f"{FIXTURE_DATABASE!r}; the serving database {SERVING_DATABASE!r} is read by the "
            "web application and is never a fixture."
        )

    import pyodbc

    from vn_air_quality_weather.sqlserver_publish import publish_serving_marts

    # The literal, not the argument. They are equal by now, but passing the constant means
    # no later edit to the check above can let a caller's string reach the connection.
    settings = Settings(sqlserver_database=FIXTURE_DATABASE)
    summary = publish_serving_marts(settings, duckdb_path=database)
    loaded_at = dt.datetime.now(dt.UTC).isoformat()
    with pyodbc.connect(settings.require_sqlserver_odbc(), autocommit=False) as sql:
        cursor = sql.cursor()
        cursor.execute(
            "IF OBJECT_ID(N'dbo.fidelity_snapshot', 'U') IS NOT NULL "
            "DROP TABLE dbo.fidelity_snapshot"
        )
        cursor.execute(
            "CREATE TABLE dbo.fidelity_snapshot ("
            " snapshot_id varchar(64) NOT NULL,"
            " pinned_clock_utc datetimeoffset(6) NOT NULL,"
            " loaded_at_utc datetimeoffset(6) NOT NULL)"
        )
        # Bound as ISO-8601 text: pyodbc applies the client's own offset to an aware
        # datetime on some paths, which is how 06:00 UTC was once stored as 06:00+07:00.
        cursor.execute(
            "INSERT INTO dbo.fidelity_snapshot VALUES (?, ?, ?)",
            manifest["snapshotId"],
            manifest["pinnedClockUtc"],
            loaded_at,
        )
        sql.commit()
    return summary.total_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument(
        "--refresh-snapshot",
        action="store_true",
        help="re-materialise the snapshot from the real warehouse at the current clock",
    )
    parser.add_argument(
        "--load-fixture-db",
        action="store_true",
        help=f"publish the snapshot into the isolated SQL Server database ({FIXTURE_DATABASE})",
    )
    # No --fixture-database option: the target is not configurable, so there is no
    # argument a typo or a pasted connection fragment can reach the publisher through.
    args = parser.parse_args(argv)

    if args.refresh_snapshot:
        warehouse = Path(Settings().duckdb_path)
        if not warehouse.exists():
            raise SystemExit(f"No warehouse at {warehouse}")
        assert_raw_source_health_mart_matches(warehouse)
        manifest = materialize_snapshot(warehouse, args.snapshot_dir)
        print(f"Snapshot {manifest['snapshotId'][:12]} pinned at {manifest['pinnedClockUtc']}")

    with tempfile.TemporaryDirectory() as scratch:
        database = Path(scratch) / "fidelity_snapshot.duckdb"
        manifest = import_snapshot(args.snapshot_dir, database)
        document = build_document(database, manifest)

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        queries = document["queries"]
        total = sum(q["rowCount"] for q in queries.values())
        print(
            f"Wrote {len(queries)} query fingerprints ({total:,d} rows) from snapshot "
            f"{manifest['snapshotId'][:12]} (clock {manifest['pinnedClockUtc']}) to {args.out}"
        )
        for name, q in queries.items():
            print(f"  {name:<26} {q['rowCount']:>6,d} rows  {len(q['columns']):>3d} columns")

        if args.load_fixture_db:
            rows = load_fixture_database(database, manifest, FIXTURE_DATABASE)
            print(f"Loaded {rows:,d} rows into {FIXTURE_DATABASE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
