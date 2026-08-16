"""Produce the pruned, compacted warehouse published as the demo deploy asset.

The deployed dashboard is downloaded by every cold start, so its size is a latency
cost paid by readers rather than a disk cost paid once. Forecast vintages accumulate
at roughly 1 MB per run on a six-hourly schedule (audit register, section J), and
the serving marts resolve exactly one vintage per anchor -- so every vintage except
the newest is weight the deployed app can never show.

Vintage history is NOT deleted from the local warehouse. It is the only route to a
forecast verification fact, which is the only route to publishing an accuracy figure
(roadmap Phase 6, items 3-5). This script reads the local warehouse and writes a
separate file; the source is opened read-only and never modified.

Observed history is kept in full. It is small, it does not accumulate per run, and
the History page is the one surface that needs a long series.

    python scripts/build_deploy_warehouse.py \
        --source data/warehouse/vn_air_quality_weather.duckdb \
        --output data/warehouse/deploy.duckdb

After this, run `dbt build` against the output so the analytics layer matches the
pruned raw layer. The script refuses to guess about that: it prints the command
rather than running dbt itself, because dbt is not a dependency of this file.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

import duckdb

# Raw forecast tables and the column carrying their vintage. Both are pruned to the
# newest vintage per location, independently, because air and weather come from
# different provider model runs and can legitimately disagree -- collapsing them to
# one global newest vintage would silently drop an anchor's whole weather series.
FORECAST_TABLES = (
    ("raw", "air_quality_forecast_hourly"),
    ("raw", "weather_forecast_hourly"),
)
VINTAGE_COLUMN = "forecast_issued_at_utc"
LOCATION_COLUMN = "location_key"


def _megabytes(path: Path) -> float:
    return round(path.stat().st_size / (1024 * 1024), 2)


def _table_exists(connection: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    return bool(
        connection.execute(
            "select count(*) > 0 from information_schema.tables "
            "where table_schema = ? and table_name = ?",
            [schema, table],
        ).fetchone()[0]
    )


def prune_forecast_vintages(connection: duckdb.DuckDBPyConnection) -> list[str]:
    """Delete every forecast row except the newest vintage per location."""

    report: list[str] = []
    for schema, table in FORECAST_TABLES:
        qualified = f"{schema}.{table}"
        if not _table_exists(connection, schema, table):
            report.append(f"{qualified}: absent, skipped")
            continue

        before, vintages_before = connection.execute(
            f"select count(*), count(distinct {VINTAGE_COLUMN}) from {qualified}"
        ).fetchone()
        connection.execute(
            f"""
            delete from {qualified}
            where ({LOCATION_COLUMN}, {VINTAGE_COLUMN}) not in (
                select {LOCATION_COLUMN}, max({VINTAGE_COLUMN})
                from {qualified}
                group by {LOCATION_COLUMN}
            )
            """
        )
        after, vintages_after = connection.execute(
            f"select count(*), count(distinct {VINTAGE_COLUMN}) from {qualified}"
        ).fetchone()
        report.append(
            f"{qualified}: {before} -> {after} rows, "
            f"{vintages_before} -> {vintages_after} distinct vintages"
        )
    return report


def verify_views_resolve(database: Path) -> None:
    """Query the view-materialised marts and fail loudly if their catalog is wrong.

    Without this the defect is silent at build time and only appears on the reader's
    screen: `copy from database` happily writes views whose stored SQL names a
    catalog that no longer exists, and nothing complains until something selects from
    one. Any script that rewrites this file has to prove the result is still readable.
    """

    with duckdb.connect(str(database), read_only=True) as connection:
        for relation in ("mart_current_conditions", "mart_outdoor_decision_window"):
            connection.execute(f"select count(*) from analytics.{relation}").fetchone()


def build_deploy_warehouse(source: Path, output: Path, *, compact_only: bool = False) -> None:
    """Prune, compact, or both.

    ``compact_only`` exists because dbt re-inflates the file: rebuilding the
    analytics layer against a pruned raw layer measured 13.5 MB -> 19.5 MB, since
    freed pages are reused rather than returned. Compaction therefore has to run
    *after* dbt, while pruning has to run *before* it -- otherwise the analytics
    tables are still built from every vintage and stay large no matter what the raw
    layer looks like. Two passes, in that order.
    """

    if not source.exists():
        raise FileNotFoundError(f"source warehouse not found: {source}")
    if source.resolve() == output.resolve():
        raise ValueError("source and output must differ; DuckDB cannot copy a file onto itself")
    if output.exists():
        output.unlink()

    source_mb = _megabytes(source)

    # Work on a copy so the local warehouse is never opened for writing. Airflow may
    # hold it, and DuckDB allows many readers or one writer -- not both.
    with tempfile.TemporaryDirectory(prefix="deploy-warehouse-") as directory:
        working = Path(directory) / "working.duckdb"
        shutil.copy2(source, working)

        if compact_only:
            report = ["prune skipped (--compact-only)"]
        else:
            with duckdb.connect(str(working)) as connection:
                report = prune_forecast_vintages(connection)
                connection.execute("checkpoint")

        # Deleting rows does not shrink a DuckDB file; freed pages are reused, not
        # returned. Copying the catalog into a fresh database is what reclaims the
        # space, and it is the difference between a few MB and tens of MB over the
        # wire on every cold start.
        # The filename is load-bearing, which is why this script takes an output
        # DIRECTORY and never an output name. DuckDB names a catalog after the file
        # stem, `copy from database` copies view DDL verbatim -- catalog qualifier
        # included -- and mart_current_conditions and mart_outdoor_decision_window are
        # views (audit register, finding E). Writing `deploy.duckdb` out as
        # `deploy-final.duckdb` produced views still pointing at a catalog named
        # `deploy`, and every page reading them raised `Catalog "deploy" does not
        # exist`. Renaming after the copy does not help either: the qualifier is baked
        # into the stored view text, not resolved at copy time. One filename from the
        # local warehouse through CI to the reader's download is the only thing that
        # holds.
        compacted_directory = Path(directory) / "compacted"
        compacted_directory.mkdir()
        compacted = compacted_directory / source.name

        with duckdb.connect(str(compacted)) as connection:
            # Equal to source.stem by construction, which is the whole point: it is
            # the catalog name the copied view definitions already refer to. Quoted
            # because a stem like `vn_air_quality_weather` is a bare identifier but a
            # hyphenated one is not.
            destination = connection.execute("select current_database()").fetchone()[0]
            quoted = '"' + destination.replace('"', '""') + '"'
            connection.execute(f"attach '{working}' as pruned (read_only)")
            connection.execute(f"copy from database pruned to {quoted}")
            connection.execute("detach pruned")
            connection.execute("checkpoint")

        shutil.move(str(compacted), str(output))

    verify_views_resolve(output)

    for line in report:
        print(line)
    print()
    print(f"source {source} = {source_mb} MB")
    print(f"output {output} = {_megabytes(output)} MB")
    print("view-materialised marts: readable")
    if not compact_only:
        print()
        print("Next: rebuild the analytics layer against the pruned raw layer,")
        print("then run this script again with --compact-only to reclaim the space")
        print("dbt leaves behind.")
        print(f'  $env:DUCKDB_PATH = "{output.resolve()}"')
        print("  dbt build --project-dir dbt --profiles-dir dbt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/warehouse/vn_air_quality_weather.duckdb"),
        help="Local warehouse to read. Opened read-only; never modified.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help=(
            "Directory to write into. The output ALWAYS keeps the source filename, "
            "because DuckDB bakes the catalog name into view definitions and the "
            "marts are views."
        ),
    )
    parser.add_argument(
        "--compact-only",
        action="store_true",
        help="Reclaim space without pruning. Run this after dbt, which re-inflates the file.",
    )
    arguments = parser.parse_args()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    build_deploy_warehouse(
        arguments.source,
        arguments.output_dir / arguments.source.name,
        compact_only=arguments.compact_only,
    )


if __name__ == "__main__":
    main()
