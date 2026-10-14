"""Republish the serving marts from DuckDB into SQL Server.

DuckDB remains the analytical warehouse: dbt builds into it and nothing else writes
it. SQL Server holds only the fifteen relations the web application reads, and this
module is the one bridge between them. It lives in the installed package rather
than in `scripts/` because the Airflow image installs the package and not the
scripts directory, and the DAG task and the command line have to run the same code.

    python scripts/publish_to_sqlserver.py     # command line
    from vn_air_quality_weather.sqlserver_publish import publish_serving_marts

**Why a second database at all.** DuckDB admits one writer or many readers, never
both. A web application holding a read-only pool on the warehouse file takes the
lock the 02:00 DAG needs, and the symptom is a failed pipeline rather than a slow
page -- the hardest kind of failure to trace back to its cause. Publishing into a
server that was built for concurrent readers removes the contention instead of
scheduling around it.

**Full reload, not incremental.** All fifteen relations together are roughly twelve
thousand rows. At that size an incremental strategy buys nothing and costs a merge
key per table, each of which is a place where the serving copy can silently diverge
from the mart. Every run drops, recreates and refills every table inside a single
transaction, so the published state is always exactly one dbt build and never a
blend of two.

**The schema is derived, never hand-written.** Column names, order and types come
from `information_schema` in DuckDB, and the string widths come from measuring the
data. A hand-maintained DDL file would be a second description of a schema dbt
already owns, free to drift from it the first time a model changes.

**What this script refuses to do.** It does not reshape, rename or filter anything.
Every column of every relation crosses as it stands, `source_type` and
`coverage_tier` included, because a consumer that cannot tell a modelled estimate
from an observation is the failure this project spends the most effort avoiding.
Timestamps stay in UTC; converting to Asia/Ho_Chi_Minh is the presentation layer's
job and happens no earlier.

**Run this on a short timer, not only after a DAG.** Five of the fifteen serving
relations are dbt *views* that read `current_timestamp` on every query, and they are
views on purpose: `mart_current_conditions` picks the forecast hour nearest to now,
`mart_outdoor_decision_window` ranks the hours still ahead, and both models carry a
comment recording that they were tables once and froze at build time, recommending
hours that had already elapsed. Copying a live view into a static table reintroduces
exactly that defect -- the page would report FRESH for a snapshot taken six hours
earlier, which is the one failure this project's freshness columns exist to prevent.

The fix is cadence, not reimplementation. This script costs about a second, so it
runs every few minutes rather than every six hours, and the frozen `as_of_utc` is
never more than that lag old. The alternative -- rewriting five dbt models as T-SQL
views over the published tables -- would put a second copy of the decision logic in
a second language, free to disagree with the first. dbt stays the only place that
logic lives.

Because the clock moves every run, `version_id` advances only when the *content*
changes: a hash excludes the continuously moving ages and `as_of_utc`, so a reader
is not woken by every timer tick. Freshness and exhausted-horizon transitions are
included: they change whether the same readings are safe to present as current.

Credentials come from the environment or `.env` and never from this file. See
`Settings.require_sqlserver_odbc`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyodbc

from vn_air_quality_weather.settings import Settings

# Identifiers reach SQL Server bracketed, but bracketing is not validation: a name
# containing a closing bracket would still escape the quoting. Everything here comes
# from a constant list or from DuckDB's catalogue, so a name that fails this pattern
# means something is wrong upstream and the run should stop rather than continue.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SOURCE_SCHEMA = "analytics"
TARGET_SCHEMA = "dbo"

# int64 bounds. DuckDB's HUGEINT is 128-bit and SQL Server has no equivalent, so the
# values are checked rather than assumed to fit. Today they are counts of hours in
# the low hundreds; the check is what keeps that from being a permanent assumption.
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

TYPE_MAP: dict[str, str] = {
    "BOOLEAN": "bit",
    "TINYINT": "smallint",
    "SMALLINT": "smallint",
    "INTEGER": "int",
    "BIGINT": "bigint",
    "HUGEINT": "bigint",
    "FLOAT": "float(24)",
    "DOUBLE": "float(53)",
    "DATE": "date",
    "TIMESTAMP": "datetime2(6)",
    "TIMESTAMP WITH TIME ZONE": "datetimeoffset(6)",
}

# Measured widths decide between a narrow, indexable column and a wide one. The
# threshold is not arbitrary: SQL Server caps an index key at 900 bytes, so a column
# that might carry prose cannot be part of one, and a key column that is silently
# widened to 4000 would make its index illegal the day someone adds it.
NARROW_MAX_OBSERVED = 64
NARROW_WIDTH = 128
WIDE_WIDTH = 4000

INSERT_CHUNK_ROWS = 1_000


@dataclass(frozen=True, slots=True)
class Relation:
    """One serving relation and the index the web application reads it through."""

    name: str
    index_columns: tuple[str, ...]
    unique: bool = False


# The contract of what the web serves. A relation absent from this list is one the
# application cannot read, which is the intended way to keep a mart internal:
# mart_flagged_measurement_quarantine and the raw layer are deliberately not here.
SERVING_RELATIONS: tuple[Relation, ...] = (
    Relation("dim_province", ("province_code",), unique=True),
    Relation("dim_vn_aqi_category", ("band_index",), unique=True),
    Relation("mart_current_conditions", ("location_key",), unique=True),
    Relation("mart_location_hourly_forecast", ("location_key", "valid_at_utc")),
    Relation("mart_outdoor_decision_window", ("location_key", "suitability_rank")),
    Relation("mart_outdoor_contiguous_window", ("location_key", "duration_hours")),
    Relation("mart_city_aqi_hourly", ("city_key", "observed_at_utc")),
    Relation("mart_city_aqi_daily", ("city_key", "data_date_vn")),
    Relation("mart_city_air_quality_hourly", ("city_key", "observed_at_utc", "pollutant")),
    Relation("mart_data_coverage", ("city_key", "data_date_utc")),
    Relation("fct_pipeline_run", ("finished_at_utc",)),
    # Four rows aggregated from 227,000 raw ones. It is published, and the raw tables
    # behind it are not, because the pipeline-health page needs the counts and nothing
    # needs the rows.
    Relation("mart_raw_source_health", ("source",), unique=True),
    Relation("mart_forecast_vs_analysis", ("location_key", "pollutant", "lead_band")),
    Relation("mart_model_station_discrepancy", ("location_key", "pollutant", "lead_band")),
    Relation("mart_station_representativeness", ("station_id", "pollutant")),
)

# Clock-dependent values cannot be compared strictly by the value/fingerprint gates
# across source reads taken at different instants. Keep this set for those gates:
# even discrete freshness or expiry states can cross a boundary between two reads.
CLOCK_DERIVED_COLUMNS = frozenset(
    {
        "as_of_utc",
        "data_age_minutes",
        "forecast_age_minutes",
        "is_forecast_horizon_exhausted",
        "freshness_status",
    }
)

# Versioning has a different promise from cross-database value verification. Ages
# and as_of move on every tick, but FRESH -> STALE and an exhausted horizon change
# the advice a reader may use, even if every concentration stayed identical. Those
# states must wake open pages. Forecast-row changes and elapsed outdoor hours are
# also content and remain included, just as before.
VERSION_EXCLUDED_COLUMNS = frozenset({"as_of_utc", "data_age_minutes", "forecast_age_minutes"})

VERSION_TABLE = "warehouse_version"

# The control row Spring polls. Not dropped with the marts: it is the one piece of
# state that has to survive a republish, because its version_id is what tells a
# reader that the rest of the database changed underneath it.
#
# CHECK (lock_row = 1) enforces the single-row shape in the schema rather than in
# the code that writes it. A publisher that inserted instead of updating would
# otherwise leave two versions in a table every reader treats as one.
VERSION_COLUMNS = (
    "lock_row",
    "version_id",
    "content_hash",
    "published_at",
    "refreshed_at",
    "forecast_vintage",
    "row_count",
)

VERSION_DDL = f"""
CREATE TABLE [{TARGET_SCHEMA}].[{VERSION_TABLE}] (
    lock_row         bit               NOT NULL CONSTRAINT PK_warehouse_version PRIMARY KEY
                                       CONSTRAINT CK_warehouse_version_single CHECK (lock_row = 1),
    version_id       bigint            NOT NULL,
    content_hash     char(64)          NOT NULL,
    published_at     datetimeoffset(6) NOT NULL,
    refreshed_at     datetimeoffset(6) NOT NULL,
    forecast_vintage datetimeoffset(6) NULL,
    row_count        int               NOT NULL
)
"""


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    duckdb_type: str
    sql_type: str


@dataclass(frozen=True, slots=True)
class TablePlan:
    """Everything needed to recreate one relation, resolved before anything is written."""

    relation: Relation
    columns: tuple[Column, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def name(self) -> str:
        return self.relation.name


def check_identifier(name: str, what: str) -> str:
    if not IDENTIFIER.match(name):
        raise ValueError(f"Refusing to build SQL for {what} with unsafe name {name!r}")
    return name


def duck(name: str) -> str:
    """Quote an identifier for DuckDB.

    DuckDB quotes identifiers with double quotes and reads square brackets as list
    construction, so `select [province_code]` is not a quoted column at all -- it
    returns a one-element list per row, silently, with no error. That is how every
    value in this script's first draft arrived wrapped in a list and every measured
    string width came back as 1. The two dialects are kept in two functions so the
    difference has to be spelled rather than remembered.
    """

    return f'"{check_identifier(name, "identifier")}"'


def mssql(name: str) -> str:
    """Quote an identifier for SQL Server, which uses square brackets."""

    return f"[{check_identifier(name, 'identifier')}]"


def measure_varchar_widths(
    connection: duckdb.DuckDBPyConnection,
    relation: str,
    columns: list[str],
) -> dict[str, int | None]:
    """Return the longest value in each VARCHAR column, or None where there is none.

    None is a real answer and not a missing one: three serving marts are legitimately
    empty until a pipeline run fills them, and an empty column has no width to read.
    """

    if not columns:
        return {}
    projection = ", ".join(f"max(length({duck(c)}))" for c in columns)
    row = connection.execute(
        f"select {projection} from {duck(SOURCE_SCHEMA)}.{duck(relation)}"
    ).fetchone()
    return dict(zip(columns, row or [None] * len(columns), strict=True))


def resolve_varchar_type(observed: int | None, fallback: int | None) -> str:
    """Pick a width from measurement, falling back to the same column in another mart.

    `city_key` is eleven characters wide in the marts that have rows, so the copy in
    an empty mart should be narrow too. Without the fallback every column of an empty
    relation would widen to 4000 and its index would become illegal the moment the
    relation filled up -- a failure that would appear days after this run, in a
    different script.
    """

    width = observed if observed is not None else fallback
    if width is None or width > NARROW_MAX_OBSERVED:
        return f"nvarchar({WIDE_WIDTH})"
    return f"nvarchar({NARROW_WIDTH})"


def build_plans(connection: duckdb.DuckDBPyConnection) -> list[TablePlan]:
    """Read every serving relation and resolve its target schema before writing anything.

    Reading finishes before the SQL Server transaction opens. The publish then holds
    schema locks for as short a time as possible, and a warehouse that turns out to be
    missing a relation fails before the serving copy has been touched.
    """

    catalogue: dict[str, list[tuple[str, str]]] = {}
    for relation in SERVING_RELATIONS:
        check_identifier(relation.name, "relation")
        rows = connection.execute(
            """
            select column_name, data_type
            from information_schema.columns
            where table_schema = ? and table_name = ?
            order by ordinal_position
            """,
            [SOURCE_SCHEMA, relation.name],
        ).fetchall()
        if not rows:
            raise ValueError(
                f"{SOURCE_SCHEMA}.{relation.name} is not in the warehouse. "
                "Run `dbt build` before publishing."
            )
        catalogue[relation.name] = [(str(name), str(kind)) for name, kind in rows]

    # One pass over every relation first, so a narrow width measured anywhere can
    # inform the same column name in a relation that happens to be empty.
    widest: dict[str, int] = {}
    measured: dict[str, dict[str, int | None]] = {}
    for name, columns in catalogue.items():
        varchars = [c for c, kind in columns if kind == "VARCHAR"]
        measured[name] = measure_varchar_widths(connection, name, varchars)
        for column, observed in measured[name].items():
            if observed is not None:
                widest[column] = max(widest.get(column, 0), int(observed))

    plans: list[TablePlan] = []
    for relation in SERVING_RELATIONS:
        columns: list[Column] = []
        for column_name, duckdb_type in catalogue[relation.name]:
            check_identifier(column_name, "column")
            if duckdb_type == "VARCHAR":
                sql_type = resolve_varchar_type(
                    measured[relation.name].get(column_name),
                    widest.get(column_name),
                )
            elif duckdb_type in TYPE_MAP:
                sql_type = TYPE_MAP[duckdb_type]
            else:
                raise ValueError(
                    f"No SQL Server type mapped for {relation.name}.{column_name} "
                    f"({duckdb_type}). Add it to TYPE_MAP deliberately rather than "
                    "letting the publish guess."
                )
            columns.append(Column(column_name, duckdb_type, sql_type))

        present = {c.name for c in columns}
        missing = [c for c in relation.index_columns if c not in present]
        if missing:
            raise ValueError(
                f"{relation.name} has no column(s) {missing} to index. "
                "The index definition in SERVING_RELATIONS is stale."
            )

        projection = ", ".join(duck(c.name) for c in columns)
        raw = connection.execute(
            f"select {projection} from {duck(SOURCE_SCHEMA)}.{duck(relation.name)}"
        ).fetchall()
        plans.append(
            TablePlan(relation, tuple(columns), tuple(coerce_row(r, columns) for r in raw))
        )
    return plans


def coerce_row(row: tuple[Any, ...], columns: list[Column]) -> tuple[Any, ...]:
    """Make one row bindable, refusing values the target type cannot hold.

    Two conversions are real rather than defensive. A 128-bit integer that does not
    fit int64 has no honest representation in SQL Server, and NaN or infinity in a
    float column would be rejected by the driver with a message that names neither
    the column nor the row.
    """

    coerced: list[Any] = []
    for value, column in zip(row, columns, strict=True):
        if value is None:
            coerced.append(None)
        elif isinstance(value, dt.datetime) and value.tzinfo is not None:
            # Normalised to UTC rather than passed through. The project stores UTC
            # and converts to Asia/Ho_Chi_Minh only to display, while DuckDB returns
            # these in its session zone (+07:00) -- passing them through would put
            # local time in a column named _utc. Kept as a datetime here so the
            # content hash and the vintage comparison still work on instants; the
            # conversion to a bindable value happens in `to_parameter`.
            coerced.append(value.astimezone(dt.UTC))
        elif isinstance(value, list | dict | tuple | set):
            # A composite value has no column type here to receive it, and reaching
            # this branch has meant a malformed query rather than a genuine nested
            # column: DuckDB reads `[col]` as list construction, so one wrong quoting
            # character turns every scalar in the relation into a one-element list.
            # Checked for every column rather than for the few that could plausibly
            # be nested, because the failure it catches affects all of them at once.
            raise ValueError(
                f"{column.name} produced {type(value).__name__} {value!r} where a "
                f"scalar {column.duckdb_type} was expected. The query that read it "
                "is wrong, or the mart grew a nested column this script cannot map."
            )
        elif column.duckdb_type == "HUGEINT" and not (INT64_MIN <= value <= INT64_MAX):
            raise ValueError(
                f"{column.name} holds {value}, which does not fit a SQL Server bigint."
            )
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError(
                f"{column.name} holds {value!r}. SQL Server float stores no NaN or "
                "infinity, and writing it as NULL would report a missing reading "
                "where the warehouse holds a broken one."
            )
        else:
            coerced.append(value)
    return tuple(coerced)


def qualified(name: str) -> str:
    return f"{mssql(TARGET_SCHEMA)}.{mssql(name)}"


def to_parameter(value: Any) -> Any:
    """Render a value in the one form both of pyodbc's binding paths agree on.

    An aware datetime is bound as ISO-8601 text and converted by SQL Server, rather
    than handed to the driver as a datetime. Measured, not assumed: with
    `fast_executemany` on, pyodbc ignores the value's own tzinfo and stamps the
    client machine's offset onto the wall clock, so 06:00 UTC is stored as
    06:00+07:00 -- a different instant, seven hours out, with no error. The same
    value bound through the ordinary path is stored correctly, which is how the
    marts and the control row ended up disagreeing about one vintage.

    Text removes the ambiguity: `2026-08-16 06:00:04.714908+00:00` says which instant
    it is, and SQL Server parses it the same way whichever path delivered it. The
    explicit `timespec` keeps the format fixed-width, so the value is also stable to
    compare and to hash.
    """

    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.isoformat(sep=" ", timespec="microseconds")
    return value


def create_table_sql(plan: TablePlan) -> str:
    body = ",\n    ".join(f"{mssql(c.name)} {c.sql_type} NULL" for c in plan.columns)
    return f"CREATE TABLE {qualified(plan.name)} (\n    {body}\n)"


def create_index_sql(plan: TablePlan) -> str:
    kind = "UNIQUE " if plan.relation.unique else ""
    columns = ", ".join(mssql(c) for c in plan.relation.index_columns)
    return f"CREATE {kind}INDEX {mssql('IX_' + plan.name)} ON {qualified(plan.name)} ({columns})"


def publish(cursor: pyodbc.Cursor, plans: list[TablePlan]) -> int:
    """Drop, recreate and refill every serving table. Returns the total row count."""

    total = 0
    for plan in plans:
        cursor.execute(
            f"IF OBJECT_ID(N'{qualified(plan.name)}', 'U') IS NOT NULL "
            f"DROP TABLE {qualified(plan.name)}"
        )
        cursor.execute(create_table_sql(plan))

        if plan.rows:
            placeholders = ", ".join("?" for _ in plan.columns)
            names = ", ".join(mssql(c.name) for c in plan.columns)
            statement = f"INSERT INTO {qualified(plan.name)} ({names}) VALUES ({placeholders})"
            for start in range(0, len(plan.rows), INSERT_CHUNK_ROWS):
                cursor.executemany(
                    statement,
                    [
                        tuple(to_parameter(v) for v in row)
                        for row in plan.rows[start : start + INSERT_CHUNK_ROWS]
                    ],
                )

        # After the rows, so the unique indexes are checked against the data that was
        # actually published rather than against an empty table.
        cursor.execute(create_index_sql(plan))
        total += len(plan.rows)
    return total


def latest_vintage(plans: list[TablePlan]) -> dt.datetime | None:
    """Return the newest forecast vintage published, for the control row."""

    for plan in plans:
        if plan.name != "mart_current_conditions":
            continue
        index = {c.name: i for i, c in enumerate(plan.columns)}.get("forecast_issued_at_utc")
        if index is None:
            return None
        stamps = [row[index] for row in plan.rows if row[index] is not None]
        # The rows are already coerced, so these are UTC. Read from the published
        # plan rather than queried separately, so the control row can never name a
        # vintage that is not in the table it describes.
        return max(stamps) if stamps else None
    return None


def canonical(value: Any) -> str:
    """Render one value as text that is identical for identical data.

    Shared by the content hash here and by the verification script's comparison, so
    the two never disagree about what "the same value" means. `repr` on a float
    round-trips an IEEE double exactly, and an aware datetime is normalised to UTC
    so two spellings of the same instant hash alike.
    """

    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return f"naive:{value.isoformat(timespec='microseconds')}"
        return f"utc:{value.astimezone(dt.UTC).isoformat(timespec='microseconds')}"
    if isinstance(value, dt.date):
        return f"date:{value.isoformat()}"
    if isinstance(value, float):
        return f"f:{value!r}"
    if isinstance(value, int):
        return f"i:{value}"
    return f"s:{value}"


def content_hash(plans: list[TablePlan]) -> str:
    """Hash readings and usable-data states, excluding continuously moving ages.

    Rows are sorted before hashing: DuckDB is free to return an unordered relation in
    a different order between reads, and an order-sensitive hash would report new
    content every time it did.
    """

    digest = hashlib.sha256()
    for plan in plans:
        keep = [i for i, c in enumerate(plan.columns) if c.name not in VERSION_EXCLUDED_COLUMNS]
        digest.update(plan.name.encode())
        for line in sorted("\x1f".join(canonical(row[i]) for i in keep) for row in plan.rows):
            digest.update(b"\x1e")
            digest.update(line.encode())
    return digest.hexdigest()


def ensure_version_table(cursor: pyodbc.Cursor) -> int:
    """Create the control table, rebuilding it when its shape is out of date.

    Returns the version id already reached, so a rebuild never hands a reader a
    number lower than one it has seen. A migration tool would be the answer for a
    table with data worth keeping; this one holds a single row that the next publish
    rewrites anyway.
    """

    existing = {
        str(row[0])
        for row in cursor.execute(
            "SELECT c.name FROM sys.columns c "
            "JOIN sys.tables t ON t.object_id = c.object_id "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "WHERE s.name = ? AND t.name = ?",
            TARGET_SCHEMA,
            VERSION_TABLE,
        ).fetchall()
    }
    if existing == set(VERSION_COLUMNS):
        return 0
    carried = 0
    if existing:
        if "version_id" in existing:
            row = cursor.execute(
                f"SELECT MAX(version_id) FROM {qualified(VERSION_TABLE)}"
            ).fetchone()
            carried = int(row[0]) if row and row[0] is not None else 0
        cursor.execute(f"DROP TABLE {qualified(VERSION_TABLE)}")
    cursor.execute(VERSION_DDL)
    return carried


def publish_version(
    cursor: pyodbc.Cursor,
    carried: int,
    digest: str,
    vintage: dt.datetime | None,
    rows: int,
) -> tuple[int, bool]:
    """Record this publish, advancing the version only when the content moved.

    Returns the version id and whether it changed. `refreshed_at` always moves, so an
    operator can tell a publisher that is running and finding nothing new from one
    that has stopped running at all -- two states that look identical if only the
    version is recorded.
    """

    now = dt.datetime.now(dt.UTC)
    current = cursor.execute(
        f"SELECT version_id, content_hash FROM {qualified(VERSION_TABLE)}"
    ).fetchone()

    if current is None:
        version = carried + 1
        cursor.execute(
            f"INSERT INTO {qualified(VERSION_TABLE)} "
            f"({', '.join(mssql(c) for c in VERSION_COLUMNS)}) "
            "VALUES (1, ?, ?, ?, ?, ?, ?)",
            version,
            digest,
            to_parameter(now),
            to_parameter(now),
            to_parameter(vintage),
            rows,
        )
        return version, True

    if str(current[1]) == digest:
        cursor.execute(
            f"UPDATE {qualified(VERSION_TABLE)} SET refreshed_at = ?, "
            "forecast_vintage = ?, row_count = ?",
            to_parameter(now),
            to_parameter(vintage),
            rows,
        )
        return int(current[0]), False

    version = int(current[0]) + 1
    cursor.execute(
        f"UPDATE {qualified(VERSION_TABLE)} SET version_id = ?, content_hash = ?, "
        "published_at = ?, refreshed_at = ?, forecast_vintage = ?, row_count = ?",
        version,
        digest,
        to_parameter(now),
        to_parameter(now),
        to_parameter(vintage),
        rows,
    )
    return version, True


@dataclass(frozen=True, slots=True)
class RelationSummary:
    """What one relation contributed to a publish."""

    name: str
    rows: int
    columns: int


@dataclass(frozen=True, slots=True)
class PublishSummary:
    """The result of one publish, in a shape an Airflow XCom can carry."""

    relations: tuple[RelationSummary, ...]
    total_rows: int
    version_id: int
    content_changed: bool
    forecast_vintage: str | None
    database: str

    def as_dict(self) -> dict[str, Any]:
        """Render for XCom, which serialises to JSON and cannot carry a dataclass."""

        return {
            "relations": [
                {"name": r.name, "rows": r.rows, "columns": r.columns} for r in self.relations
            ],
            "total_rows": self.total_rows,
            "version_id": self.version_id,
            "content_changed": self.content_changed,
            "forecast_vintage": self.forecast_vintage,
            "database": self.database,
        }


def read_plans(duckdb_path: Path) -> list[TablePlan]:
    """Resolve every serving relation from the warehouse.

    Read-only and short-lived. The warehouse belongs to whichever process is writing
    it, and this one is never that process -- but DuckDB lets a reader block a writer,
    so in Airflow this work still holds the single-slot warehouse pool.

    One source transaction pins current_timestamp for every serving view. Without
    it, separate autocommit reads can straddle an hour or expiry boundary and publish
    tables that disagree about which forecast hour is current. The transaction ends
    before the SQL Server transaction starts; the target publish remains atomic.
    """

    if not duckdb_path.exists():
        raise FileNotFoundError(f"No warehouse at {duckdb_path}")
    with duckdb.connect(str(duckdb_path), read_only=True) as connection:
        connection.begin()
        try:
            plans = build_plans(connection)
        except Exception:
            connection.rollback()
            raise
        connection.commit()
        return plans


def publish_serving_marts(
    settings: Settings | None = None,
    duckdb_path: Path | None = None,
) -> PublishSummary:
    """Republish every serving relation into SQL Server, and return what happened.

    One function for both callers, so the DAG task and the command line can never
    drift into publishing different things. The whole republish is a single
    transaction: SQL Server makes DDL transactional, so a failure halfway leaves
    readers on the previous published state rather than on a half-built one.
    """

    settings = settings or Settings()
    plans = read_plans(Path(duckdb_path or settings.duckdb_path))
    vintage = latest_vintage(plans)

    with pyodbc.connect(settings.require_sqlserver_odbc(), autocommit=False) as sql:
        cursor = sql.cursor()
        cursor.fast_executemany = True
        carried = ensure_version_table(cursor)
        published = publish(cursor, plans)
        version, changed = publish_version(cursor, carried, content_hash(plans), vintage, published)
        sql.commit()

    return PublishSummary(
        relations=tuple(RelationSummary(p.name, len(p.rows), len(p.columns)) for p in plans),
        total_rows=published,
        version_id=version,
        content_changed=changed,
        forecast_vintage=None if vintage is None else vintage.isoformat(),
        database=settings.sqlserver_database,
    )
