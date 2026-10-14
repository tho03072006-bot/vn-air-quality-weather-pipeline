"""Command line for the DuckDB to SQL Server publish.

The logic lives in `vn_air_quality_weather.sqlserver_publish`, not here, because the
Airflow image installs the package and not this directory -- so a DAG task and a
shell both run the same code rather than two copies of it. This file is the terminal
interface: argument parsing, a readable table, and an exit code.

    python scripts/publish_to_sqlserver.py
    python scripts/publish_to_sqlserver.py --dry-run
    python scripts/publish_to_sqlserver.py --duckdb-path data/warehouse/demo.duckdb

Credentials come from the environment or `.env` and never from a committed file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vn_air_quality_weather.settings import Settings
from vn_air_quality_weather.sqlserver_publish import (
    latest_vintage,
    publish_serving_marts,
    read_plans,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--duckdb-path",
        type=Path,
        default=None,
        help="Warehouse to publish from. Defaults to the configured DUCKDB_PATH.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read the warehouse and print the plan without touching SQL Server.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings()
    source = Path(args.duckdb_path or settings.duckdb_path)

    if args.dry_run:
        # The read half only, so the plan can be inspected on a machine that has no
        # SQL Server to publish into.
        try:
            plans = read_plans(source)
        except FileNotFoundError as error:
            print(error, file=sys.stderr)
            return 1
        report(
            [(p.name, len(p.rows), len(p.columns)) for p in plans],
            sum(len(p.rows) for p in plans),
        )
        vintage = latest_vintage(plans)
        print(f"\nDry run: SQL Server was not contacted. Vintage would be {vintage}.")
        return 0

    try:
        summary = publish_serving_marts(settings, source)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1

    report(
        [(r.name, r.rows, r.columns) for r in summary.relations],
        summary.total_rows,
    )
    state = "new content" if summary.content_changed else "unchanged content, clock refreshed"
    print(
        f"\nPublished {summary.total_rows:,d} rows into {summary.database} "
        f"at version {summary.version_id} ({state})."
    )
    return 0


def report(rows: list[tuple[str, int, int]], total: int) -> None:
    width = max(len(name) for name, _, _ in rows)
    for name, count, columns in rows:
        empty = "  (empty -- run the pipeline to fill it)" if not count else ""
        print(f"  {name:<{width}}  {count:>6,d} rows  {columns:>3d} cols{empty}")
    print(f"  {'TOTAL':<{width}}  {total:>6,d} rows")


if __name__ == "__main__":
    raise SystemExit(main())
