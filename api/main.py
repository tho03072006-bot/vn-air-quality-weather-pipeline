"""A read-only HTTP view of the marts, with the same promises the UI makes.

Run it from the project root, the way the dashboard is run:

    uvicorn api.main:app --reload

What this is: dbt marts, served. Every response is a slice of a relation dbt
already built, passed through without recomputation. There is no business logic
here to drift from the warehouse, and there is none in the dashboard either -- both
read the same marts through the same `dashboard.data_access` queries.

**What it will not do, which is the part worth stating.** The product publishes no
forecast-accuracy figure -- no MAE, no RMSE, no bias -- because none exists that
would be honest. `confidence_level` is derived from lead time, field completeness
and whether air and weather came from the same model run; it is not accuracy, and
this API must never present it as such. `mart_model_station_discrepancy` measures a
CAMS grid cell against one street-level station, which is model error and
representativeness error added together and not yet separable, and it is not
exposed here at all: publishing it over an API, stripped of the caveats the Trust
page renders beside it, is how a measured limitation becomes a quoted accuracy
number in someone else's chart.

Every row keeps `source_type` and `coverage_tier`, so a consumer can always tell a
modelled estimate from an observation, and every forecast keeps
`forecast_issued_at_utc`, so a consumer can always tell which model run it is
reading. Those columns are not optional decoration; they are the contract.

Read-only throughout. `dashboard.data_access` opens every connection with
`read_only=True`, which is also what makes it safe to run this beside Airflow:
DuckDB permits many readers or one writer, and this process is never the writer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query

from dashboard.data_access import (
    load_current_conditions,
    load_location_forecast,
    load_pipeline_runs,
    load_provinces,
    relation_exists,
)
from vn_air_quality_weather.settings import get_settings

API_VERSION = "1"

# The relations this API reads. Checked rather than assumed: the warehouse ships as
# a release asset built by a separate workflow on a separate schedule, so an asset
# that predates a mart is a real state and not a hypothetical one.
REQUIRED_RELATIONS = (
    "dim_province",
    "mart_current_conditions",
    "mart_location_hourly_forecast",
    "fct_pipeline_run",
)

# Attached once per response rather than to every row. A consumer that reads only
# the numbers will still meet these in the payload it parses.
DISCLOSURE: dict[str, str] = {
    "anchor": (
        "Each province is represented by a single CAMS model grid point. One point "
        "does not describe a whole province, and the values are not interpolated to "
        "street level."
    ),
    "not_a_station": (
        "A representative point is not a monitoring station and is never labelled as "
        "one. Rows carry source_type and coverage_tier; MODELED_ONLY means no "
        "suitable observation exists for that area."
    ),
    "issued_at": (
        "forecast_issued_at_utc is when this system fetched the model run, not when "
        "the provider produced it. The upstream API does not report the latter."
    ),
    "outdoor_score": (
        "outdoor_score is a transparent planning heuristic over PM2.5, rain "
        "probability, apparent temperature and UV. It is not VN_AQI under Decision "
        "1459/QD-TCMT and it is not a health index."
    ),
    "confidence_is_not_accuracy": (
        "confidence_level derives from lead time, field completeness and whether air "
        "and weather share one model run. It is not forecast accuracy. This API "
        "publishes no accuracy figure: no MAE, no RMSE, no bias."
    ),
}


def warehouse_path() -> Path:
    """Resolve the warehouse the same way every other entry point does.

    A FastAPI dependency rather than a module constant so a test can point one app
    at a fixture without touching the environment of the process running it.
    """

    return Path(get_settings().duckdb_path)


# Module scope, not inside create_app, and that is load-bearing. `from __future__
# import annotations` turns every annotation into a string, and FastAPI resolves
# those strings against the function's module globals. An alias defined inside the
# factory is invisible there, so FastAPI cannot see the Depends, silently reclassifies
# the parameter as a query string, and every route answers 422 instead of running.
WarehouseDep = Annotated[Path, Depends(warehouse_path)]


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Serialise a mart slice without reshaping it.

    pandas' own JSON writer, not a hand-rolled type map: it already renders NaN as
    null and tz-aware timestamps as ISO-8601, and every column here arrives from a
    dbt model whose types are already settled. A bespoke converter would be a second
    description of those types, free to disagree with the first.
    """

    if frame.empty:
        return []
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _readable_relations(path: Path) -> dict[str, bool] | None:
    """Report which required relations exist, or None when the file cannot be read."""

    if not path.exists():
        return None
    try:
        return {name: relation_exists(path, "analytics", name) for name in REQUIRED_RELATIONS}
    except Exception:  # noqa: BLE001 - a corrupt or locked file is the same answer
        return None


def _require_warehouse(path: Path) -> None:
    """Refuse the request rather than answer it from a warehouse that is not there.

    503 rather than 500: nothing is wrong with the request, and nothing here can fix
    it. The warehouse is published separately and this process is a reader.
    """

    relations = _readable_relations(path)
    if relations is None or not all(relations.values()):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "warehouse_unavailable",
                "message": (
                    "The published warehouse is missing or incomplete, so no mart can "
                    "be served. This is a data-availability state, not a bad request."
                ),
                "relations": relations,
            },
        )


def _location_key(path: Path, province_code: str) -> str:
    """Map a province code to the key the marts are grained on, or 404."""

    provinces = load_provinces(path)
    match = provinces.loc[provinces["province_code"] == province_code]
    if match.empty:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "unknown_province",
                "message": f"No province registered with code {province_code!r}.",
            },
        )
    return str(match.iloc[0]["province_key"])


def create_app() -> FastAPI:
    app = FastAPI(
        title="Vietnam air quality and weather — read API",
        version=API_VERSION,
        summary="Read-only access to the published marts. Publishes no accuracy figure.",
    )

    @app.get("/v1/health", tags=["health"])
    def health(path: WarehouseDep) -> dict[str, Any]:
        """Report whether the warehouse can be served, and how old it is.

        Answers with 503 when it cannot. A health endpoint that returns 200 while the
        thing it reports on is unreadable is a check that cannot fail.
        """

        relations = _readable_relations(path)
        if relations is None or not all(relations.values()):
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "warehouse_unavailable",
                    "relations": relations,
                    "disclosure": DISCLOSURE,
                },
            )

        current = load_current_conditions(path)
        provinces = load_provinces(path)
        runs = load_pipeline_runs(path, limit=1)

        freshness = current["freshness_status"].value_counts().to_dict()
        exhausted = current.get("is_forecast_horizon_exhausted")
        return {
            "warehouse_available": True,
            "relations": relations,
            "provinces_registered": int(len(provinces)),
            "provinces_with_forecast": int(current["province_code"].nunique()),
            "freshness_status_counts": {str(k): int(v) for k, v in freshness.items()},
            "oldest_forecast_age_minutes": _optional_int(current["forecast_age_minutes"].max()),
            "newest_forecast_issued_at_utc": _optional_iso(current["forecast_issued_at_utc"].max()),
            # A frozen warehouse outlives its own horizon: the vintage stops moving
            # while the clock does not, and the serving marts empty out from the near
            # hours first. Counting it here means an operator sees it before a reader.
            "locations_past_forecast_horizon": (
                0 if exhausted is None else int(exhausted.fillna(False).astype(bool).sum())
            ),
            "latest_pipeline_run": (_records(runs)[0] if not runs.empty else None),
            "disclosure": DISCLOSURE,
        }

    @app.get("/v1/current", tags=["current"])
    def current_conditions_all(path: WarehouseDep) -> dict[str, Any]:
        """Latest modelled conditions for every province with a forecast."""

        _require_warehouse(path)
        frame = load_current_conditions(path)
        return {"count": int(len(frame)), "items": _records(frame), "disclosure": DISCLOSURE}

    @app.get("/v1/current/{province_code}", tags=["current"])
    def current_conditions_one(path: WarehouseDep, province_code: str) -> dict[str, Any]:
        """Latest modelled conditions for one province."""

        _require_warehouse(path)
        key = _location_key(path, province_code)
        frame = load_current_conditions(path, key)
        if frame.empty:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "no_current_conditions",
                    "message": (
                        f"Province {province_code!r} is registered but has no current "
                        "row. This is missing data, not an unknown province."
                    ),
                },
            )
        return {"item": _records(frame)[0], "disclosure": DISCLOSURE}

    @app.get("/v1/forecast/{province_code}", tags=["forecast"])
    def forecast(
        path: WarehouseDep,
        province_code: str,
        lead_hours: Annotated[
            int,
            Query(
                ge=1,
                le=72,
                description="Hours ahead to return. The published horizon is 72 hours.",
            ),
        ] = 72,
    ) -> dict[str, Any]:
        """Hourly modelled forecast for one province, with the vintage it came from."""

        _require_warehouse(path)
        key = _location_key(path, province_code)
        frame = load_location_forecast(path, key, hours=lead_hours)
        issued = _optional_iso(frame["forecast_issued_at_utc"].max()) if not frame.empty else None
        return {
            "province_code": province_code,
            "location_key": key,
            "requested_lead_hours": lead_hours,
            # Surfaced at the top as well as on every row. Which model run a forecast
            # came from is not a detail a consumer should have to dig for, and an
            # empty list with no vintage is a different statement from no answer.
            "forecast_issued_at_utc": issued,
            "count": int(len(frame)),
            "items": _records(frame),
            "disclosure": DISCLOSURE,
        }

    return app


def _optional_int(value: Any) -> int | None:
    return None if pd.isna(value) else int(value)


def _optional_iso(value: Any) -> str | None:
    return None if pd.isna(value) else pd.Timestamp(value).isoformat()


app = create_app()
