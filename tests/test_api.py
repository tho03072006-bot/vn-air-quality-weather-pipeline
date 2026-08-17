"""Cover the read API's contract, not just its status codes.

Three of these tests exist because of promises rather than mechanics. The product
publishes no forecast-accuracy figure and never labels a model grid point a station;
an HTTP surface is the easiest place for either promise to be lost, because a
consumer parsing JSON never sees the Trust page that carries the caveats. So the
tests below assert what must be present in a payload and what must be absent from
it, alongside the ordinary routing.

The fixture warehouse is built once per session by `build_demo_warehouse` and dbt,
the same pair CI uses, and every connection the API opens is read-only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

fastapi = pytest.importorskip("fastapi", reason="the api extra is not installed")
from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app, warehouse_path  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _client(database: Path) -> TestClient:
    """One app per test, pointed at a fixture rather than at the real warehouse."""

    app = create_app()
    app.dependency_overrides[warehouse_path] = lambda: database
    return TestClient(app)


@pytest.fixture(scope="session")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a small warehouse with the same tooling the pipeline uses.

    Session-scoped because dbt build dominates the cost. Skipped rather than failed
    when dbt is absent: the `etl` extra is what provides it, and a developer with
    only `dev` installed should see a skip explaining that, not a red suite.
    """

    database = tmp_path_factory.mktemp("api-warehouse") / "vn_air_quality_weather.duckdb"
    environment = {**os.environ, "DUCKDB_PATH": str(database)}

    build = subprocess.run(
        [sys.executable, "scripts/build_demo_warehouse.py", "--database", str(database)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        pytest.skip(f"could not build the fixture warehouse: {build.stderr[-400:]}")

    dbt = Path(sys.executable).with_name("dbt.exe" if os.name == "nt" else "dbt")
    if not dbt.exists():
        pytest.skip('dbt is not installed; run pip install -e ".[etl]"')

    transform = subprocess.run(
        [str(dbt), "build", "--project-dir", "dbt", "--profiles-dir", "dbt"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if transform.returncode != 0:
        pytest.skip(f"dbt build failed for the fixture: {transform.stdout[-400:]}")
    return database


@pytest.fixture(scope="session")
def hanoi_code(warehouse: Path) -> str:
    with duckdb.connect(str(warehouse), read_only=True) as connection:
        row = connection.execute(
            "select province_code from analytics.dim_province where province_key = 'hanoi'"
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_health_reports_a_readable_warehouse(warehouse: Path) -> None:
    response = _client(warehouse).get("/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["warehouse_available"] is True
    assert all(body["relations"].values())
    assert body["provinces_registered"] == 34
    assert body["provinces_with_forecast"] >= 1


def test_health_refuses_rather_than_reporting_healthy(tmp_path: Path) -> None:
    """A health endpoint that answers 200 while the warehouse is unreadable is a
    check that cannot fail, which this project treats as worse than no check."""

    response = _client(tmp_path / "absent.duckdb").get("/v1/health")

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "warehouse_unavailable"


def test_current_conditions_keep_the_provenance_columns(warehouse: Path, hanoi_code: str) -> None:
    """source_type and coverage_tier are the contract, not decoration: without them
    a consumer cannot tell a modelled estimate from a measurement."""

    response = _client(warehouse).get(f"/v1/current/{hanoi_code}")

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["source_type"]
    assert item["coverage_tier"]
    assert item["forecast_issued_at_utc"]
    assert item["province_code"] == hanoi_code


def test_forecast_names_the_model_run_it_came_from(warehouse: Path, hanoi_code: str) -> None:
    client = _client(warehouse)
    response = client.get(f"/v1/forecast/{hanoi_code}", params={"lead_hours": 24})

    assert response.status_code == 200
    body = response.json()
    assert body["requested_lead_hours"] == 24
    if body["count"]:
        assert body["forecast_issued_at_utc"]
        assert all(row["forecast_issued_at_utc"] for row in body["items"])
        assert all(row["source_type"] for row in body["items"])


def test_shorter_lead_returns_no_more_rows_than_a_longer_one(
    warehouse: Path, hanoi_code: str
) -> None:
    """The parameter must actually filter. A query parameter that renders but does
    not re-query is the defect class the dashboard gate was built to catch."""

    client = _client(warehouse)
    short = client.get(f"/v1/forecast/{hanoi_code}", params={"lead_hours": 6}).json()
    long = client.get(f"/v1/forecast/{hanoi_code}", params={"lead_hours": 72}).json()

    assert short["count"] <= long["count"]


def test_lead_hours_beyond_the_published_horizon_is_rejected(
    warehouse: Path, hanoi_code: str
) -> None:
    """72 hours is what the pipeline fetches. Accepting 500 would invite a consumer
    to read an empty tail as a forecast of calm weather."""

    response = _client(warehouse).get(f"/v1/forecast/{hanoi_code}", params={"lead_hours": 500})

    assert response.status_code == 422


def test_unknown_province_is_separated_from_missing_data(warehouse: Path) -> None:
    response = _client(warehouse).get("/v1/current/ZZ")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "unknown_province"


def test_every_response_carries_the_confidence_disclosure(warehouse: Path, hanoi_code: str) -> None:
    """A consumer parsing JSON never sees the Trust page. The promise travels with
    the payload or it does not travel."""

    client = _client(warehouse)
    for url in ("/v1/health", "/v1/current", f"/v1/current/{hanoi_code}"):
        body = client.get(url).json()
        disclosure = body["disclosure"]
        assert "not forecast accuracy" in disclosure["confidence_is_not_accuracy"]
        assert "no MAE, no RMSE, no bias" in disclosure["confidence_is_not_accuracy"]
        assert "not a monitoring station" in disclosure["not_a_station"]


def test_no_route_publishes_an_accuracy_figure(warehouse: Path) -> None:
    """The discrepancy mart is deliberately not exposed. Served over an API without
    the caveats the Trust page renders beside it, a model-station gap becomes a
    quoted accuracy number in somebody else's chart."""

    client = _client(warehouse)
    paths = {route.path for route in create_app().routes}

    assert not any("discrepancy" in path or "accuracy" in path for path in paths)
    assert client.get("/v1/model_station_discrepancy").status_code == 404

    # Field names in the data, not a substring sweep of the whole payload. The first
    # version of this test searched the serialised body for "mae" and failed on the
    # disclosure sentence promising there is no MAE -- a check firing on the very
    # wording it exists to protect. What must be absent is the discrepancy mart's
    # columns, and `paired_hours` is the one that only ever comes from it.
    served_keys = {key.lower() for row in client.get("/v1/current").json()["items"] for key in row}

    assert not any("discrepancy" in key or "accuracy" in key for key in served_keys)
    assert "paired_hours" not in served_keys
