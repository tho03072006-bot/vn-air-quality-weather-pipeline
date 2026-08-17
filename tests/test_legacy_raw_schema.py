"""Staging must build against the schema immutable history was written with.

The published warehouse is a release asset rebuilt every six hours by a workflow
that downloads the previous asset and runs dbt over it. Its `raw` tables therefore
carry whatever columns existed when their rows were written, and adding a column to
the ingestion does not retroactively add it there.

Adding `station_latitude` to `stg_air_quality_measurements` broke that workflow for
exactly this reason, and the failure named neither the missing column nor the schema
missing it:

    Binder Error: Column "station_latitude" referenced that exists in the SELECT
    clause - but this column cannot be referenced before it is defined

DuckDB resolved the reference to the alias of the same name. Every local run stayed
green because the fixture is regenerated from current code and always has the column,
so nothing between the developer and the deployment could see the problem.

This test is that missing step: build the fixture, remove the newest raw columns to
recreate an older warehouse, and require dbt to build anyway.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Columns added to the ingestion after warehouses already existed. Each one is a
# column some stored raw table does not have, and the list is the record of which
# additions a pre-existing warehouse has to survive.
COLUMNS_ADDED_AFTER_FIRST_LOAD = {
    "air_quality_hourly": ("station_latitude", "station_longitude"),
}


def _dbt_executable() -> Path | None:
    name = "dbt.exe" if os.name == "nt" else "dbt"
    candidate = Path(sys.executable).with_name(name)
    return candidate if candidate.exists() else None


@pytest.fixture(scope="module")
def legacy_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A fixture warehouse with the newest raw columns removed."""

    dbt = _dbt_executable()
    if dbt is None:
        pytest.skip('dbt is not installed; run pip install -e ".[etl]"')

    database = tmp_path_factory.mktemp("legacy") / "vn_air_quality_weather.duckdb"
    build = subprocess.run(
        [sys.executable, "scripts/build_demo_warehouse.py", "--database", str(database)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        pytest.skip(f"could not build the fixture: {build.stderr[-300:]}")

    with duckdb.connect(str(database)) as connection:
        for table, columns in COLUMNS_ADDED_AFTER_FIRST_LOAD.items():
            for column in columns:
                connection.execute(f"alter table raw.{table} drop column {column}")
    return database


def test_dbt_builds_against_a_warehouse_without_the_newest_columns(
    legacy_warehouse: Path,
) -> None:
    dbt = _dbt_executable()
    assert dbt is not None

    result = subprocess.run(
        [str(dbt), "build", "--project-dir", "dbt", "--profiles-dir", "dbt"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DUCKDB_PATH": str(legacy_warehouse)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "dbt cannot build against a warehouse written before the newest raw columns "
        "existed, which is what every published asset is:\n" + result.stdout[-2000:]
    )


def test_absent_positions_degrade_rather_than_break(legacy_warehouse: Path) -> None:
    """A warehouse with no station positions must say so, not guess or fail.

    The distinction matters downstream: a station with no position cannot have the
    model sampled at it, and reporting that as an absent comparison is honest where
    reporting it as a zero displacement would not be.
    """

    with duckdb.connect(str(legacy_warehouse), read_only=True) as connection:
        rows = connection.execute(
            "select has_position, count(*) from analytics.dim_air_quality_station group by 1"
        ).fetchall()

    assert rows, "the station dimension is empty on a legacy warehouse"
    assert all(not has_position for has_position, _ in rows)


def test_the_current_fixture_does_have_positions(tmp_path: Path) -> None:
    """The guard above must not pass by measuring nothing.

    If the fixture ever stopped generating coordinates, every assertion about their
    absence would still hold and the legacy test would certify a pipeline that had
    quietly stopped capturing them.
    """

    shutil.copy(PROJECT_ROOT / "dbt" / "seeds" / "provinces_2025.csv", tmp_path / "seed.csv")
    from scripts.build_demo_warehouse import OBSERVED_STATIONS

    assert OBSERVED_STATIONS, "the fixture defines no observed stations"
    for station in OBSERVED_STATIONS.values():
        offset = station[3]
        assert offset != (0.0, 0.0), (
            "a fixture station sitting exactly on the anchor makes the "
            "representativeness term identically zero, which cannot be told apart "
            "from a decomposition that never computed it"
        )
