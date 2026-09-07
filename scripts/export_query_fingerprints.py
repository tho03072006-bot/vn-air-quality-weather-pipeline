"""Fingerprint every serving query, so the Java translation can be checked against it.

The web application is being rewritten in Spring, and its repositories restate the
sixteen queries in `vn_air_quality_weather/serving_queries.py` in T-SQL. A restatement
can be wrong in ways that compile, connect and return rows: a predicate dropped, an
ordering lost, two columns swapped between fields of the same type. None of those
raise anything.

This script runs each query against DuckDB and writes what it returned, reduced to a
shape that can be compared across two languages:

* the row count, and
* per column, the non-null count and a digest over that column's values, sorted.

Sorted, so the digest does not depend on row order, which the two engines are free to
choose differently for anything the query does not explicitly order. Per column rather
than per row, so a failure names the column that disagrees instead of only saying that
something did.

**Values are canonicalised, not formatted.** Floats go through `%.6f` rather than
`repr`: both languages round the same binary double to the same six decimals, while
their shortest-round-trip spellings differ (`1e-05` against `1.0E-5`). Timestamps
become UTC instants, so two spellings of one moment are one string.

The clock-dependent query records the window it used. `load_location_forecast` bounds
itself on `date_trunc('hour', current_timestamp)`, so without pinning that boundary the
two sides would compare different hours and disagree for a reason that is not a defect.

    python scripts/export_query_fingerprints.py
    python scripts/export_query_fingerprints.py --out <the Java project's test resources>

The Java test reads the file and skips itself, with the command to produce it, when it
is absent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from vn_air_quality_weather import serving_queries as data_access
from vn_air_quality_weather.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUT = PROJECT_ROOT / "data" / "fixtures" / "query_fingerprints.json"

# Columns whose value is a reading of the clock rather than a fact. Excluded from the
# comparison for the same reason the publish excludes them from its content hash: two
# reads seconds apart legitimately differ, and a gate that fails on that trains its
# reader to ignore it.
CLOCK_DERIVED = frozenset(
    {
        "as_of_utc",
        "data_age_minutes",
        "forecast_age_minutes",
        "is_forecast_horizon_exhausted",
        "freshness_status",
    }
)


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
        return f"f:{value:.6f}"
    if isinstance(value, (int,)):
        return f"i:{value}"
    return f"s:{value}"


def fingerprint(frame: pd.DataFrame) -> dict[str, Any]:
    """Reduce one result set to a comparable shape."""

    columns: dict[str, Any] = {}
    for name in frame.columns:
        if name in CLOCK_DERIVED:
            continue
        rendered = sorted(canonical(v) for v in frame[name].tolist())
        digest = hashlib.sha256("\x1e".join(rendered).encode()).hexdigest()
        columns[str(name)] = {
            "nonNull": int(sum(1 for r in rendered if r != "NULL")),
            "digest": digest,
        }
    return {"rowCount": int(len(frame)), "columns": columns}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    settings = Settings()
    path = Path(settings.duckdb_path)
    if not path.exists():
        raise SystemExit(f"No warehouse at {path}")

    # Parameters are read from the data rather than hard-coded, so the fixture stays
    # valid as the warehouse grows and so no query is fingerprinted over zero rows by
    # accident.
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

    # The instant the forecast window is anchored on, pinned rather than read from the
    # clock. Chosen from the data: one hour after the earliest forecast hour this
    # location has, so the window is full rather than empty. On a warehouse whose
    # horizon has already elapsed -- which is its normal state between pipeline runs --
    # anchoring on now would compare nothing against nothing and pass by being empty.
    with duckdb.connect(str(path), read_only=True) as connection:
        earliest = connection.execute(
            "select min(valid_at_utc) from analytics.mart_location_hourly_forecast "
            "where location_key = ?",
            [location_key],
        ).fetchone()[0]
    hour_start = (
        pd.Timestamp.now(tz="UTC").floor("h")
        if earliest is None
        else pd.Timestamp(earliest).tz_convert("UTC").floor("h") + pd.Timedelta(hours=1)
    )

    queries: dict[str, Any] = {}

    queries["provinces"] = fingerprint(provinces)
    queries["currentConditionsAll"] = fingerprint(data_access.load_current_conditions(path))
    queries["currentConditionsOne"] = fingerprint(
        data_access.load_current_conditions(path, location_key)
    )
    queries["locationForecast"] = fingerprint(
        data_access.load_location_forecast(
            path, location_key, hours=72, as_of=hour_start.to_pydatetime()
        )
    )
    queries["decisionWindows"] = fingerprint(
        data_access.load_decision_windows(path, location_key, limit=5)
    )
    queries["contiguousWindows"] = fingerprint(
        data_access.load_contiguous_windows(path, location_key, limit_per_duration=5)
    )
    queries["rawSourceHealth"] = fingerprint(data_access.load_pipeline_health(path))
    queries["pipelineRuns"] = fingerprint(data_access.load_pipeline_runs(path, limit=20))
    queries["aqiHourly"] = fingerprint(
        data_access.load_aqi_hourly(path, start, end, tuple(cities), source_type)
    )
    queries["aqiDaily"] = fingerprint(
        data_access.load_aqi_daily(path, start, end, tuple(cities), source_type)
    )
    queries["cityPollutantHourly"] = fingerprint(
        data_access.load_hourly_mart(path, start, end, tuple(cities), pollutant, source_type)
    )
    queries["coverage"] = fingerprint(
        data_access.load_coverage(path, start, end, tuple(cities), pollutant, source_type)
    )
    queries["forecastVsAnalysis"] = fingerprint(data_access.load_forecast_vs_analysis(path))
    queries["modelStationDiscrepancy"] = fingerprint(
        data_access.load_model_station_discrepancy(path)
    )

    document = {
        "generatedAtUtc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "warehouse": str(path),
        "parameters": {
            "locationKey": location_key,
            "cities": cities,
            "pollutant": pollutant,
            "sourceType": source_type,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "forecastAsOfUtc": hour_start.isoformat(),
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

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")

    total = sum(q["rowCount"] for q in queries.values())
    print(f"Wrote {len(queries)} query fingerprints ({total:,d} rows) to {args.out}")
    for name, q in queries.items():
        print(f"  {name:<26} {q['rowCount']:>6,d} rows  {len(q['columns']):>3d} columns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
