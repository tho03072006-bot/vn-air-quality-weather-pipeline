from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.sdk import dag, get_current_context, task
from airflow.timetables.interval import CronDataIntervalTimetable

from vn_air_quality_weather.airflow_callbacks import log_task_failure
from vn_air_quality_weather.pipeline import run_day, run_dbt_build
from vn_air_quality_weather.settings import WAREHOUSE_WRITER_POOL, Settings
from vn_air_quality_weather.sqlserver_publish import publish_serving_marts


@dag(
    dag_id="vn_air_quality_weather_daily",
    # extract_store_and_load derives its data date from data_interval_start, so
    # this DAG needs a real data interval. A bare cron string becomes a zero-length
    # CronTriggerTimetable in Airflow 3 and would fetch the still-unfinished run day.
    schedule=CronDataIntervalTimetable("0 2 * * *", timezone="UTC"),
    # Start at the leading edge of the real observation gap: the warehouse is
    # current through 2026-08-08. The project began earlier, but that is not a
    # useful boundary for catchup because no observations exist before 2026-07-27.
    start_date=pendulum.datetime(2026, 8, 8, tz="UTC"),
    catchup=True,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        # The failure this retries is usually an OpenAQ rate limit or a timeout,
        # and both take longer to clear than they take to hit. A flat five minutes
        # retries into the same closed window twice; backing off spreads the two
        # attempts over 15 minutes instead of 10. The HTTP client already backs off
        # within a single task -- this is the same policy one level up.
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(minutes=30),
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

    @task(execution_timeout=timedelta(minutes=10), pool=WAREHOUSE_WRITER_POOL)
    def publish_serving_copy(summary: dict[str, object]) -> dict[str, object]:
        """Push the rebuilt marts into SQL Server so the web application sees them.

        Holds the warehouse pool although it only reads. DuckDB lets a reader block a
        writer, so opening the file while the other DAG is inside dbt build would
        either fail here or stall there.

        Not the only publisher, and not the important one. The same function runs on a
        short timer, because five serving relations are views that read
        current_timestamp and freeze the moment they are copied. This task exists so a
        finished run reaches the page in seconds instead of at the next tick.
        """

        result = publish_serving_marts(Settings())
        print(
            "serving_published "
            f"rows={result.total_rows} version={result.version_id} "
            f"changed={result.content_changed} vintage={result.forecast_vintage}"
        )
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
    log_result(publish_serving_copy(build_analytics(loaded)))


vn_air_quality_weather_daily()
