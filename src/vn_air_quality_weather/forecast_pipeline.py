"""Ingest versioned 72-hour modeled forecasts for province anchors."""

import argparse
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from vn_air_quality_weather.clients.open_meteo import (
    AIR_QUALITY_FORECAST_VARIABLES,
    WEATHER_FORECAST_VARIABLES,
    OpenMeteoClient,
    normalize_air_quality_forecast,
    normalize_weather_forecast,
)
from vn_air_quality_weather.geography import CORE_LOCATION_KEYS, PROVINCES, Province
from vn_air_quality_weather.loaders.duckdb_loader import LoadSummary, load_incremental
from vn_air_quality_weather.models import (
    AirQualityForecastHourly,
    PipelineRunAudit,
    WeatherForecastHourly,
)
from vn_air_quality_weather.pipeline import create_raw_store, run_dbt_build
from vn_air_quality_weather.settings import Settings, get_settings
from vn_air_quality_weather.storage.raw_json import RawJsonStore, build_raw_envelope

LOGGER = logging.getLogger(__name__)

PIPELINE_NAME = "forecast"
STATUS_SUCCESS = "SUCCESS"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"

ERROR_SUMMARY_MAX_CHARS = 500
# Strips the query string from any URL an exception message carries. Open-Meteo's
# forecast endpoints take no credential, but an error summary is persisted and
# then rendered in the dashboard, so it must not become a place where a key can
# leak in from a future caller.
_URL_WITH_QUERY = re.compile(r"(https?://[^\s?]+)\?\S*")


class ForecastLocationError(RuntimeError):
    """One anchor failed. Raised inside a worker, never out of run_forecast."""


@dataclass(frozen=True, slots=True)
class LocationOutcome:
    location_key: str
    succeeded: bool
    raw_objects_attempted: int = 0
    raw_objects_created: int = 0
    raw_objects_reused: int = 0
    error_category: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class ForecastRunSummary:
    run_id: str
    location_count: int
    raw_objects: int
    weather_forecast_rows: int
    air_quality_forecast_rows: int
    forecast_issued_at_utc: datetime
    load_summary: LoadSummary
    status: str = STATUS_SUCCESS
    raw_objects_created: int = 0
    raw_objects_reused: int = 0
    succeeded_location_keys: tuple[str, ...] = ()
    failed_location_keys: tuple[str, ...] = ()


def redact(message: str) -> str:
    """Drop query strings so a persisted error can never carry a credential."""

    return _URL_WITH_QUERY.sub(r"\1?<redacted>", message)


def _summarize_failures(outcomes: tuple[LocationOutcome, ...]) -> tuple[str, str]:
    """Build a safe (category, summary) pair naming the anchors that failed."""

    failures = [outcome for outcome in outcomes if not outcome.succeeded]
    if not failures:
        return "", ""

    categories = sorted({outcome.error_category for outcome in failures})
    category = categories[0] if len(categories) == 1 else "MIXED"
    detail = "; ".join(f"{outcome.location_key} ({outcome.error_category})" for outcome in failures)
    # Naming the failed anchors is what makes a resume possible: rerun with
    # --province for each key rather than replaying the whole country.
    summary = f"{len(failures)} location(s) failed: {detail}"
    return category, redact(summary)[:ERROR_SUMMARY_MAX_CHARS]


def _ingest_province(
    province: Province,
    *,
    client: OpenMeteoClient,
    raw_store: RawJsonStore,
    issued_at: datetime,
    interval_end: datetime,
    forecast_hours: int,
    run_id: str,
) -> tuple[list[AirQualityForecastHourly], list[WeatherForecastHourly], LocationOutcome]:
    """Fetch and land one anchor, converting any failure into an outcome."""

    location = province.as_city()
    attempted = 0
    created = 0
    reused = 0
    try:
        LOGGER.info(
            "extract source=open_meteo_forecast location=%s hours=%d",
            location.key,
            forecast_hours,
        )
        air_payload = client.fetch_air_quality_forecast(location, forecast_hours)
        weather_payload = client.fetch_weather_forecast(location, forecast_hours)
        air_records = normalize_air_quality_forecast(
            location.key, province.code, issued_at, air_payload
        )
        weather_records = normalize_weather_forecast(
            location.key, province.code, issued_at, weather_payload
        )

        for source, variables, payload in (
            ("open_meteo_air_quality_forecast", AIR_QUALITY_FORECAST_VARIABLES, air_payload),
            ("open_meteo_weather_forecast", WEATHER_FORECAST_VARIABLES, weather_payload),
        ):
            result = raw_store.write(
                source=source,
                city_key=location.key,
                ingestion_date=issued_at.date(),
                run_id=run_id,
                payload=build_raw_envelope(
                    source=source,
                    city_key=location.key,
                    requested_at=issued_at,
                    interval_start=issued_at,
                    interval_end=interval_end,
                    run_id=run_id,
                    request_parameters={
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                        "hourly": variables,
                        "forecast_hours": forecast_hours,
                        "timezone": "GMT",
                    },
                    response=payload,
                ),
            )
            attempted += 1
            if result.created:
                created += 1
            else:
                reused += 1
    except Exception as error:  # noqa: BLE001 - one anchor must not end the run
        LOGGER.warning(
            "forecast location failed location=%s error=%s",
            location.key,
            type(error).__name__,
        )
        return (
            [],
            [],
            LocationOutcome(
                location_key=location.key,
                succeeded=False,
                raw_objects_attempted=attempted,
                raw_objects_created=created,
                raw_objects_reused=reused,
                error_category=type(error).__name__,
                error_message=redact(str(error)),
            ),
        )

    return (
        air_records,
        weather_records,
        LocationOutcome(
            location_key=location.key,
            succeeded=True,
            raw_objects_attempted=attempted,
            raw_objects_created=created,
            raw_objects_reused=reused,
        ),
    )


def run_forecast(
    *,
    provinces: tuple[Province, ...],
    forecast_hours: int = 72,
    settings: Settings | None = None,
    raw_store: RawJsonStore | None = None,
    run_id: str | None = None,
    forecast_issued_at_utc: datetime | None = None,
    max_workers: int | None = None,
) -> ForecastRunSummary:
    """Fetch and persist one immutable forecast vintage for selected anchors.

    Anchors are independent, so one failing anchor is recorded and skipped rather
    than aborting the run. Whatever landed is still loaded and the run is audited
    as PARTIAL. Only a run in which every anchor failed raises, so that Airflow
    retries a genuinely dead run instead of a merely incomplete one.
    """

    if not provinces:
        raise ValueError("at least one province is required")
    if not 1 <= forecast_hours <= 168:
        raise ValueError("forecast_hours must be between 1 and 168")

    settings = settings or get_settings()
    raw_store = raw_store or create_raw_store(settings)
    issued_at = forecast_issued_at_utc or datetime.now(UTC)
    if issued_at.tzinfo is None:
        raise ValueError("forecast_issued_at_utc must be timezone-aware")
    issued_at = issued_at.astimezone(UTC)
    run_id = run_id or f"forecast-{issued_at:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    interval_end = issued_at + timedelta(hours=forecast_hours)
    workers = max(1, min(max_workers or settings.forecast_max_workers, len(provinces)))
    started_at = datetime.now(UTC)

    weather_records: list[WeatherForecastHourly] = []
    air_quality_records: list[AirQualityForecastHourly] = []
    outcomes: list[LocationOutcome] = []

    with OpenMeteoClient(
        timeout_seconds=settings.http_timeout_seconds,
        retry_policy=settings.retry_policy(),
    ) as client:

        def ingest(province: Province):
            return _ingest_province(
                province,
                client=client,
                raw_store=raw_store,
                issued_at=issued_at,
                interval_end=interval_end,
                forecast_hours=forecast_hours,
                run_id=run_id,
            )

        if workers == 1:
            results = [ingest(province) for province in provinces]
        else:
            # map preserves input order, so the loaded record order stays
            # deterministic regardless of which anchor returns first.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(ingest, provinces))

    for air_records, province_weather, outcome in results:
        air_quality_records.extend(air_records)
        weather_records.extend(province_weather)
        outcomes.append(outcome)

    succeeded = tuple(outcome.location_key for outcome in outcomes if outcome.succeeded)
    failed = tuple(outcome.location_key for outcome in outcomes if not outcome.succeeded)
    if not succeeded:
        status = STATUS_FAILED
    elif failed:
        status = STATUS_PARTIAL
    else:
        status = STATUS_SUCCESS

    load_summary = load_incremental(
        database_path=settings.duckdb_path,
        weather=[],
        observed_air_quality=[],
        modeled_air_quality=[],
        weather_forecasts=weather_records,
        air_quality_forecasts=air_quality_records,
        pipeline_name="vn_air_quality_weather_forecast",
    )

    attempted = sum(outcome.raw_objects_attempted for outcome in outcomes)
    created = sum(outcome.raw_objects_created for outcome in outcomes)
    reused = sum(outcome.raw_objects_reused for outcome in outcomes)
    error_category, error_summary = _summarize_failures(tuple(outcomes))
    finished_at = datetime.now(UTC)

    load_incremental(
        database_path=settings.duckdb_path,
        weather=[],
        observed_air_quality=[],
        modeled_air_quality=[],
        pipeline_runs=[
            PipelineRunAudit(
                run_id=run_id,
                # A vintage spans 72 hours and has no single data date, so the
                # audit records the date it was issued on.
                data_date=issued_at.date(),
                started_at_utc=started_at,
                finished_at_utc=finished_at,
                duration_seconds=(finished_at - started_at).total_seconds(),
                raw_backend=settings.raw_backend,
                include_openaq=False,
                raw_objects=attempted,
                weather_rows=0,
                observed_air_quality_rows=0,
                modeled_air_quality_rows=0,
                pipeline_name=PIPELINE_NAME,
                status=status,
                requested_location_count=len(provinces),
                succeeded_location_count=len(succeeded),
                failed_location_count=len(failed),
                raw_objects_created=created,
                raw_objects_reused=reused,
                weather_forecast_rows=load_summary.weather_forecast_rows,
                air_quality_forecast_rows=load_summary.air_quality_forecast_rows,
                error_category=error_category,
                error_summary=error_summary,
            )
        ],
        pipeline_name="vn_air_quality_weather_forecast",
    )

    LOGGER.info(
        "forecast run=%s status=%s locations=%d/%d weather=%d air_quality=%d "
        "raw_created=%d raw_reused=%d",
        run_id,
        status,
        len(succeeded),
        len(provinces),
        load_summary.weather_forecast_rows,
        load_summary.air_quality_forecast_rows,
        created,
        reused,
    )

    summary = ForecastRunSummary(
        run_id=run_id,
        location_count=len(provinces),
        raw_objects=attempted,
        weather_forecast_rows=load_summary.weather_forecast_rows,
        air_quality_forecast_rows=load_summary.air_quality_forecast_rows,
        forecast_issued_at_utc=issued_at,
        load_summary=load_summary,
        status=status,
        raw_objects_created=created,
        raw_objects_reused=reused,
        succeeded_location_keys=succeeded,
        failed_location_keys=failed,
    )

    if status == STATUS_FAILED:
        # Audited first, then raised, so the warehouse records the dead run and
        # Airflow still sees a failed task worth retrying.
        raise ForecastLocationError(error_summary or "every location failed")
    return summary


def selected_provinces(keys: list[str] | None, all_provinces: bool) -> tuple[Province, ...]:
    if all_provinces and keys:
        raise ValueError("use --all-provinces or --province, not both")
    selected_keys = tuple(PROVINCES) if all_provinces else tuple(keys or CORE_LOCATION_KEYS)
    unknown = sorted(set(selected_keys) - set(PROVINCES))
    if unknown:
        raise ValueError(f"unknown province keys: {', '.join(unknown)}")
    return tuple(PROVINCES[key] for key in selected_keys)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest versioned national forecasts")
    parser.add_argument("--province", action="append", dest="provinces")
    parser.add_argument("--all-provinces", action="store_true")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--max-workers", type=int, default=None)
    parser.add_argument("--skip-dbt", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    arguments = _parse_args()
    try:
        provinces = selected_provinces(arguments.provinces, arguments.all_provinces)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    settings = get_settings()
    summary = run_forecast(
        provinces=provinces,
        forecast_hours=arguments.hours,
        settings=settings,
        max_workers=arguments.max_workers,
    )
    if not arguments.skip_dbt:
        run_dbt_build(settings.duckdb_path, project_root=Path(__file__).resolve().parents[2])
    print(
        f"forecast run={summary.run_id} status={summary.status} "
        f"locations={len(summary.succeeded_location_keys)}/{summary.location_count} "
        f"weather={summary.weather_forecast_rows} "
        f"air_quality={summary.air_quality_forecast_rows} "
        f"raw_created={summary.raw_objects_created} raw_reused={summary.raw_objects_reused}"
    )
    if summary.failed_location_keys:
        resume = " ".join(f"--province {key}" for key in summary.failed_location_keys)
        print(f"failed locations: {', '.join(summary.failed_location_keys)}")
        print(f"resume with: python -m vn_air_quality_weather.forecast_pipeline {resume}")


if __name__ == "__main__":
    main()
