from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, get_current_context, task

from vn_air_quality_weather.airflow_callbacks import log_task_failure
from vn_air_quality_weather.pipeline import run_day, run_dbt_build
from vn_air_quality_weather.settings import WAREHOUSE_WRITER_POOL, Settings


@dag(
    dag_id="vn_air_quality_weather_daily",
    schedule="0 2 * * *",
    start_date=pendulum.datetime(2026, 7, 1, tz="UTC"),
    catchup=True,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": log_task_failure,
    },
    tags=["air-quality", "weather", "vietnam"],
)
def vn_air_quality_weather_daily():
    @task
    def validate_configuration() -> dict[str, str]:
        settings = Settings()
        settings.require_openaq_api_key()
        if settings.raw_backend == "s3":
            settings.require_s3()
        return {
            "raw_backend": settings.raw_backend,
            "duckdb_path": str(settings.duckdb_path),
        }

    @task(execution_timeout=timedelta(minutes=45), pool=WAREHOUSE_WRITER_POOL)
    def extract_store_and_load(_: dict[str, str]) -> dict[str, object]:
        context = get_current_context()
        data_interval_start = context["data_interval_start"]
        summary = run_day(
            data_interval_start.date(),
            settings=Settings(),
            run_id=str(context["run_id"]),
            include_openaq=True,
        )
        result = asdict(summary)
        result["data_date"] = summary.data_date.isoformat()
        result["load_summary"]["database_path"] = str(summary.load_summary.database_path)
        return result

    @task(execution_timeout=timedelta(minutes=20), pool=WAREHOUSE_WRITER_POOL)
    def build_analytics(summary: dict[str, object]) -> dict[str, object]:
        settings = Settings()
        run_dbt_build(settings.duckdb_path, project_root=Path("/opt/project"))
        return summary

    @task
    def log_result(summary: dict[str, object]) -> None:
        print(
            "pipeline_complete "
            f"date={summary['data_date']} raw={summary['raw_objects']} "
            f"weather={summary['weather_rows']} "
            f"observed_aq={summary['observed_air_quality_rows']} "
            f"modeled_aq={summary['modeled_air_quality_rows']}"
        )

    configuration = validate_configuration()
    loaded = extract_store_and_load(configuration)
    log_result(build_analytics(loaded))


vn_air_quality_weather_daily()
