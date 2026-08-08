"""Six-hourly national forecast ingestion and analytics refresh."""

from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, get_current_context, task

from vn_air_quality_weather.airflow_callbacks import log_task_failure
from vn_air_quality_weather.forecast_pipeline import run_forecast
from vn_air_quality_weather.geography import PROVINCES
from vn_air_quality_weather.pipeline import run_dbt_build
from vn_air_quality_weather.settings import WAREHOUSE_WRITER_POOL, Settings


@dag(
    dag_id="vn_air_quality_weather_forecast",
    schedule="0 */6 * * *",
    start_date=pendulum.datetime(2026, 8, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": log_task_failure,
    },
    tags=["air-quality", "weather", "forecast", "vietnam"],
)
def vn_air_quality_weather_forecast():
    @task
    def validate_configuration() -> dict[str, str]:
        settings = Settings()
        if settings.raw_backend == "s3":
            settings.require_s3()
        return {
            "raw_backend": settings.raw_backend,
            "duckdb_path": str(settings.duckdb_path),
            "province_count": str(len(PROVINCES)),
        }

    @task(execution_timeout=timedelta(minutes=45), pool=WAREHOUSE_WRITER_POOL)
    def ingest_forecast(_: dict[str, str]) -> dict[str, object]:
        context = get_current_context()
        summary = run_forecast(
            provinces=tuple(PROVINCES.values()),
            forecast_hours=72,
            settings=Settings(),
            run_id=str(context["run_id"]),
        )
        result = asdict(summary)
        result["forecast_issued_at_utc"] = summary.forecast_issued_at_utc.isoformat()
        result["load_summary"]["database_path"] = str(summary.load_summary.database_path)
        return result

    @task(execution_timeout=timedelta(minutes=20), pool=WAREHOUSE_WRITER_POOL)
    def build_analytics(summary: dict[str, object]) -> dict[str, object]:
        settings = Settings()
        run_dbt_build(settings.duckdb_path, project_root=Path("/opt/project"))
        return summary

    @task
    def log_result(summary: dict[str, object]) -> None:
        failed = summary.get("failed_location_keys") or ()
        succeeded = summary.get("succeeded_location_keys") or ()
        print(
            "forecast_complete "
            f"status={summary['status']} "
            f"locations={len(succeeded)}/{summary['location_count']} "
            f"raw_attempted={summary['raw_objects']} "
            f"raw_created={summary['raw_objects_created']} "
            f"raw_reused={summary['raw_objects_reused']} "
            f"weather={summary['weather_forecast_rows']} "
            f"air_quality={summary['air_quality_forecast_rows']}"
        )
        if failed:
            # A PARTIAL run does not fail the task, so the anchors that need a
            # rerun have to be visible in the log or nobody will replay them.
            print(f"forecast_failed_locations={','.join(str(key) for key in failed)}")

    configuration = validate_configuration()
    loaded = ingest_forecast(configuration)
    log_result(build_analytics(loaded))


vn_air_quality_weather_forecast()
