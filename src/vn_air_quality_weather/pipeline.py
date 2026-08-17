import argparse
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3

from vn_air_quality_weather.cities import CITIES, City
from vn_air_quality_weather.clients.open_aq import (
    OpenAQClient,
    normalize_sensor_hours,
    select_city_sensors,
)
from vn_air_quality_weather.clients.open_meteo import (
    AIR_QUALITY_VARIABLES,
    WEATHER_VARIABLES,
    OpenMeteoClient,
    normalize_modeled_air_quality,
    normalize_weather,
)
from vn_air_quality_weather.loaders.duckdb_loader import LoadSummary, load_incremental
from vn_air_quality_weather.models import (
    ModeledAirQualityHourly,
    ObservedAirQualityHourly,
    PipelineRunAudit,
    StationModeledAirQualityHourly,
    WeatherHourly,
)
from vn_air_quality_weather.settings import Settings, get_settings
from vn_air_quality_weather.storage.raw_json import (
    LocalRawJsonStore,
    RawJsonStore,
    RawWriteResult,
    S3RawJsonStore,
    build_raw_envelope,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _RawObjectCounts:
    """Separates objects this run wrote from ones it found already present.

    Reuse is narrower than it first looks. The raw object key embeds the run_id
    (see storage.raw_json._object_key), so a manual replay -- which mints a new
    uuid run_id -- always writes new objects and reports created, not reused.
    Reuse happens when the same run_id writes the same payload twice, which in
    practice means a retried Airflow task, because there the run_id is Airflow's
    and stays stable across attempts. That is the case worth counting separately:
    a retry that re-fetched nothing new should not be reported as fresh ingestion.
    """

    attempted: int = 0
    created: int = 0
    reused: int = 0

    def record(self, result: RawWriteResult) -> None:
        self.attempted += 1
        if result.created:
            self.created += 1
        else:
            self.reused += 1


@dataclass(frozen=True, slots=True)
class PipelineRunSummary:
    data_date: date
    run_id: str
    raw_objects: int
    weather_rows: int
    observed_air_quality_rows: int
    modeled_air_quality_rows: int
    load_summary: LoadSummary
    raw_objects_created: int = 0
    raw_objects_reused: int = 0


def _sample_model_at_stations(
    *,
    open_meteo: OpenMeteoClient,
    raw_store: RawJsonStore,
    raw_counts: "_RawObjectCounts",
    city: City,
    selections: dict[str, Any],
    data_date: date,
    ingestion_time: datetime,
    interval_start: datetime,
    interval_end: datetime,
    run_id: str,
) -> list[StationModeledAirQualityHourly]:
    """Fetch the model at each station's own coordinates, one call per station.

    This is the measurement finding O was missing. Comparing it against the anchor
    series gives representativeness -- pure distance, needing no observation -- and
    against the station's own readings gives the model's offset where the instrument
    actually stands. Neither is available from the anchor series alone.

    One call per distinct station, not per sensor: several sensors share a position
    and the model does not care which pollutant is being asked about. Stations without
    coordinates are skipped rather than guessed at; the decomposition then reports an
    absent comparison, which is true, instead of a zero displacement, which is not.

    Open-Meteo needs no API key, so this runs on the same terms as the anchor fetch it
    sits beside.
    """

    positions: dict[str, tuple[float, float]] = {}
    for selection in selections.values():
        if selection.latitude is None or selection.longitude is None:
            continue
        positions.setdefault(selection.station_id, (selection.latitude, selection.longitude))

    records: list[StationModeledAirQualityHourly] = []
    for station_id, (latitude, longitude) in sorted(positions.items()):
        probe = City(
            key=f"{city.key}__station_{station_id}",
            display_name=f"{city.display_name} station {station_id}",
            latitude=latitude,
            longitude=longitude,
        )
        LOGGER.info(
            "extract source=open_meteo_cams_at_station city=%s station=%s date=%s",
            city.key,
            station_id,
            data_date,
        )
        payload = open_meteo.fetch_modeled_air_quality(probe, data_date)
        write_result = raw_store.write(
            source="open_meteo_air_quality_at_station",
            city_key=f"{city.key}__{station_id}",
            ingestion_date=ingestion_time.date(),
            run_id=run_id,
            payload=build_raw_envelope(
                source="open_meteo_air_quality_at_station",
                city_key=city.key,
                requested_at=ingestion_time,
                interval_start=interval_start,
                interval_end=interval_end,
                run_id=run_id,
                request_parameters={
                    "station_id": station_id,
                    "coordinates": [latitude, longitude],
                    "hourly": list(AIR_QUALITY_VARIABLES),
                },
                response=payload,
            ),
        )
        raw_counts.record(write_result)

        for record in normalize_modeled_air_quality(city.key, payload):
            records.append(
                StationModeledAirQualityHourly(
                    station_id=station_id,
                    city_key=city.key,
                    observed_at_utc=record.observed_at_utc,
                    pm2_5=record.pm2_5,
                    pm10=record.pm10,
                    nitrogen_dioxide=record.nitrogen_dioxide,
                    ozone=record.ozone,
                    station_latitude=latitude,
                    station_longitude=longitude,
                    grid_latitude=record.grid_latitude,
                    grid_longitude=record.grid_longitude,
                )
            )
    return records


def run_day(
    data_date: date,
    *,
    settings: Settings | None = None,
    raw_store: RawJsonStore | None = None,
    run_id: str | None = None,
    include_openaq: bool = True,
) -> PipelineRunSummary:
    """Extract one UTC day and merge it into the warehouse idempotently."""

    settings = settings or get_settings()
    raw_store = raw_store or create_raw_store(settings)
    run_id = run_id or f"manual-{data_date.isoformat()}-{uuid4().hex[:8]}"
    interval_start = datetime.combine(data_date, time.min, tzinfo=UTC)
    interval_end = interval_start + timedelta(days=1)
    ingestion_time = datetime.now(UTC)
    retry_policy = settings.retry_policy()

    weather_records: list[WeatherHourly] = []
    modeled_records: list[ModeledAirQualityHourly] = []
    station_modeled_records: list[StationModeledAirQualityHourly] = []
    observed_records: list[ObservedAirQualityHourly] = []
    raw_counts = _RawObjectCounts()

    with OpenMeteoClient(
        timeout_seconds=settings.http_timeout_seconds,
        retry_policy=retry_policy,
    ) as open_meteo:
        for city in CITIES.values():
            LOGGER.info("extract source=open_meteo_weather city=%s date=%s", city.key, data_date)
            weather_payload = open_meteo.fetch_weather(city, data_date)
            weather_records.extend(normalize_weather(city.key, weather_payload))
            write_result = raw_store.write(
                source="open_meteo_weather",
                city_key=city.key,
                ingestion_date=ingestion_time.date(),
                run_id=run_id,
                payload=build_raw_envelope(
                    source="open_meteo_weather",
                    city_key=city.key,
                    requested_at=ingestion_time,
                    interval_start=interval_start,
                    interval_end=interval_end,
                    run_id=run_id,
                    request_parameters={
                        "latitude": city.latitude,
                        "longitude": city.longitude,
                        "hourly": WEATHER_VARIABLES,
                        "timezone": "GMT",
                        "date": data_date.isoformat(),
                    },
                    response=weather_payload,
                ),
            )
            raw_counts.record(write_result)

            LOGGER.info("extract source=open_meteo_cams city=%s date=%s", city.key, data_date)
            modeled_payload = open_meteo.fetch_modeled_air_quality(city, data_date)
            modeled_records.extend(normalize_modeled_air_quality(city.key, modeled_payload))
            write_result = raw_store.write(
                source="open_meteo_air_quality",
                city_key=city.key,
                ingestion_date=ingestion_time.date(),
                run_id=run_id,
                payload=build_raw_envelope(
                    source="open_meteo_air_quality",
                    city_key=city.key,
                    requested_at=ingestion_time,
                    interval_start=interval_start,
                    interval_end=interval_end,
                    run_id=run_id,
                    request_parameters={
                        "latitude": city.latitude,
                        "longitude": city.longitude,
                        "hourly": AIR_QUALITY_VARIABLES,
                        "timezone": "GMT",
                        "date": data_date.isoformat(),
                    },
                    response=modeled_payload,
                ),
            )
            raw_counts.record(write_result)

    if include_openaq:
        with OpenAQClient(
            settings.require_openaq_api_key(),
            timeout_seconds=settings.http_timeout_seconds,
            retry_policy=retry_policy,
        ) as open_aq:
            for city in CITIES.values():
                LOGGER.info("discover source=openaq city=%s", city.key)
                locations_payload = open_aq.fetch_locations(city, settings.openaq_radius_meters)
                write_result = raw_store.write(
                    source="openaq_locations",
                    city_key=city.key,
                    ingestion_date=ingestion_time.date(),
                    run_id=run_id,
                    payload=build_raw_envelope(
                        source="openaq_locations",
                        city_key=city.key,
                        requested_at=ingestion_time,
                        interval_start=interval_start,
                        interval_end=interval_end,
                        run_id=run_id,
                        request_parameters={
                            "coordinates": [city.latitude, city.longitude],
                            "radius": settings.openaq_radius_meters,
                        },
                        response=locations_payload,
                    ),
                )
                raw_counts.record(write_result)

                selections = select_city_sensors(city.key, locations_payload)
                station_modeled_records.extend(
                    _sample_model_at_stations(
                        open_meteo=open_meteo,
                        raw_store=raw_store,
                        raw_counts=raw_counts,
                        city=city,
                        selections=selections,
                        data_date=data_date,
                        ingestion_time=ingestion_time,
                        interval_start=interval_start,
                        interval_end=interval_end,
                        run_id=run_id,
                    )
                )
                for selection in selections.values():
                    hours_payload = open_aq.fetch_sensor_hours(
                        selection.sensor_id, interval_start, interval_end
                    )
                    observed_records.extend(normalize_sensor_hours(selection, hours_payload))
                    write_result = raw_store.write(
                        source="openaq_sensor_hours",
                        city_key=city.key,
                        ingestion_date=ingestion_time.date(),
                        run_id=run_id,
                        payload=build_raw_envelope(
                            source="openaq_sensor_hours",
                            city_key=city.key,
                            requested_at=ingestion_time,
                            interval_start=interval_start,
                            interval_end=interval_end,
                            run_id=run_id,
                            request_parameters={
                                "station_id": selection.station_id,
                                "sensor_id": selection.sensor_id,
                                "pollutant": selection.pollutant,
                            },
                            response=hours_payload,
                        ),
                    )
                    raw_counts.record(write_result)

    load_summary = load_incremental(
        database_path=settings.duckdb_path,
        weather=weather_records,
        observed_air_quality=observed_records,
        modeled_air_quality=modeled_records,
        station_modeled_air_quality=station_modeled_records,
    )
    modeled_measurement_rows = load_summary.air_quality_rows - len(observed_records)
    finished_at = datetime.now(UTC)
    load_incremental(
        database_path=settings.duckdb_path,
        weather=[],
        observed_air_quality=[],
        modeled_air_quality=[],
        pipeline_runs=[
            PipelineRunAudit(
                run_id=run_id,
                data_date=data_date,
                started_at_utc=ingestion_time,
                finished_at_utc=finished_at,
                duration_seconds=(finished_at - ingestion_time).total_seconds(),
                raw_backend=settings.raw_backend,
                include_openaq=include_openaq,
                raw_objects=raw_counts.attempted,
                weather_rows=len(weather_records),
                observed_air_quality_rows=len(observed_records),
                modeled_air_quality_rows=modeled_measurement_rows,
                pipeline_name="historical",
                status="SUCCESS",
                requested_location_count=len(CITIES),
                succeeded_location_count=len(CITIES),
                failed_location_count=0,
                raw_objects_created=raw_counts.created,
                raw_objects_reused=raw_counts.reused,
            )
        ],
    )
    LOGGER.info(
        "loaded date=%s weather=%d observed_air_quality=%d modeled_air_quality=%d",
        data_date,
        len(weather_records),
        len(observed_records),
        modeled_measurement_rows,
    )
    return PipelineRunSummary(
        data_date=data_date,
        run_id=run_id,
        raw_objects=raw_counts.attempted,
        raw_objects_created=raw_counts.created,
        raw_objects_reused=raw_counts.reused,
        weather_rows=len(weather_records),
        observed_air_quality_rows=len(observed_records),
        modeled_air_quality_rows=modeled_measurement_rows,
        load_summary=load_summary,
    )


def create_raw_store(settings: Settings) -> RawJsonStore:
    if settings.raw_backend == "local":
        return LocalRawJsonStore(settings.local_raw_root)

    region, bucket = settings.require_s3()
    session = boto3.Session(
        profile_name=settings.aws_profile or None,
        region_name=region,
    )
    return S3RawJsonStore(bucket=bucket, s3_client=session.client("s3"))


def run_dbt_build(database_path: Path, project_root: Path | None = None) -> None:
    project_root = project_root or Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DUCKDB_PATH"] = str(database_path.resolve())
    executable_name = "dbt.exe" if os.name == "nt" else "dbt"
    environment_dbt = Path(sys.executable).with_name(executable_name)
    dbt_executable = (
        str(environment_dbt)
        if environment_dbt.is_file()
        else (shutil.which("dbt") or executable_name)
    )
    subprocess.run(
        [
            dbt_executable,
            "build",
            "--project-dir",
            str(project_root / "dbt"),
            "--profiles-dir",
            str(project_root / "dbt"),
        ],
        cwd=project_root,
        env=environment,
        check=True,
    )


def _date_range(start: date, end: date) -> list[date]:
    if start > end:
        raise ValueError("start date must be on or before end date")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Vietnam air-quality pipeline")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--skip-openaq", action="store_true")
    parser.add_argument("--skip-dbt", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    arguments = _parse_args()
    if arguments.date and (arguments.start_date or arguments.end_date):
        raise SystemExit("Use --date or --start-date/--end-date, not both")

    if arguments.date:
        dates = [arguments.date]
    elif arguments.start_date and arguments.end_date:
        dates = _date_range(arguments.start_date, arguments.end_date)
    else:
        raise SystemExit("Provide --date or both --start-date and --end-date")

    settings = get_settings()
    summaries: list[PipelineRunSummary] = []
    for data_date in dates:
        summaries.append(
            run_day(
                data_date,
                settings=settings,
                include_openaq=not arguments.skip_openaq,
            )
        )
    if not arguments.skip_dbt:
        run_dbt_build(settings.duckdb_path)

    for summary in summaries:
        print(
            f"{summary.data_date}: raw={summary.raw_objects}, "
            f"weather={summary.weather_rows}, observed_aq={summary.observed_air_quality_rows}, "
            f"modeled_aq={summary.modeled_air_quality_rows}"
        )


if __name__ == "__main__":
    main()
