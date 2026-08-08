from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import dlt

from vn_air_quality_weather.models import (
    AirQualityForecastHourly,
    ModeledAirQualityHourly,
    ObservedAirQualityHourly,
    PipelineRunAudit,
    WeatherForecastHourly,
    WeatherHourly,
)


@dataclass(frozen=True, slots=True)
class LoadSummary:
    load_ids: tuple[str, ...]
    weather_rows: int
    air_quality_rows: int
    database_path: Path
    weather_forecast_rows: int = 0
    air_quality_forecast_rows: int = 0


def load_incremental(
    *,
    database_path: Path,
    weather: list[WeatherHourly],
    observed_air_quality: list[ObservedAirQualityHourly],
    modeled_air_quality: list[ModeledAirQualityHourly],
    pipeline_runs: list[PipelineRunAudit] | None = None,
    weather_forecasts: list[WeatherForecastHourly] | None = None,
    air_quality_forecasts: list[AirQualityForecastHourly] | None = None,
    pipeline_name: str = "vn_air_quality_weather",
) -> LoadSummary:
    """Merge a batch into DuckDB using stable natural keys."""

    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    weather_data = [_serialize_dataclass(record) for record in weather]
    air_quality_data = [
        *(_serialize_dataclass(record) for record in observed_air_quality),
        *modeled_air_quality_rows(modeled_air_quality),
    ]
    run_audit_data = [_serialize_dataclass(record) for record in pipeline_runs or []]
    weather_forecast_data = [_serialize_dataclass(record) for record in weather_forecasts or []]
    air_quality_forecast_data = air_quality_forecast_rows(air_quality_forecasts or [])

    pipeline = dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=dlt.destinations.duckdb(credentials=str(database_path)),
        dataset_name="raw",
    )

    resources = []
    if weather_data:
        resources.append(
            dlt.resource(
                weather_data,
                name="weather_hourly",
                primary_key=["city_key", "observed_at_utc"],
                write_disposition="merge",
            )
        )
    if air_quality_data:
        resources.append(
            dlt.resource(
                air_quality_data,
                name="air_quality_hourly",
                columns={
                    "sensor_id": {"data_type": "bigint", "nullable": True},
                    "grid_latitude": {"data_type": "double", "nullable": True},
                    "grid_longitude": {"data_type": "double", "nullable": True},
                },
                primary_key=[
                    "city_key",
                    "station_id",
                    "pollutant",
                    "observed_at_utc",
                    "source_name",
                ],
                write_disposition="merge",
            )
        )
    if run_audit_data:
        resources.append(
            dlt.resource(
                run_audit_data,
                name="pipeline_runs",
                primary_key=["run_id", "data_date"],
                write_disposition="merge",
            )
        )
    if weather_forecast_data:
        resources.append(
            dlt.resource(
                weather_forecast_data,
                name="weather_forecast_hourly",
                primary_key=[
                    "location_key",
                    "forecast_issued_at_utc",
                    "valid_at_utc",
                    "source_name",
                ],
                write_disposition="merge",
            )
        )
    if air_quality_forecast_data:
        resources.append(
            dlt.resource(
                air_quality_forecast_data,
                name="air_quality_forecast_hourly",
                primary_key=[
                    "location_key",
                    "forecast_issued_at_utc",
                    "valid_at_utc",
                    "pollutant",
                    "source_name",
                ],
                write_disposition="merge",
            )
        )
    if not resources:
        return LoadSummary((), 0, 0, database_path)

    load_info = pipeline.run(resources)
    return LoadSummary(
        load_ids=tuple(load_info.loads_ids),
        weather_rows=len(weather_data),
        air_quality_rows=len(air_quality_data),
        database_path=database_path,
        weather_forecast_rows=len(weather_forecast_data),
        air_quality_forecast_rows=len(air_quality_forecast_data),
    )


def modeled_air_quality_rows(
    records: list[ModeledAirQualityHourly],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pollutant_fields = {
        "pm25": "pm2_5",
        "pm10": "pm10",
        "no2": "nitrogen_dioxide",
        "o3": "ozone",
    }
    for record in records:
        for pollutant, field_name in pollutant_fields.items():
            value = getattr(record, field_name)
            if value is None:
                continue
            rows.append(
                {
                    "city_key": record.city_key,
                    "station_id": "modeled_grid",
                    "station_name": "Open-Meteo CAMS grid",
                    "sensor_id": None,
                    "pollutant": pollutant,
                    "unit": "µg/m³",
                    "observed_at_utc": record.observed_at_utc,
                    "value": value,
                    "flagged": False,
                    "source_name": record.source_name,
                    "source_type": record.source_type,
                    "grid_latitude": record.grid_latitude,
                    "grid_longitude": record.grid_longitude,
                }
            )
    return rows


def air_quality_forecast_rows(
    records: list[AirQualityForecastHourly],
) -> list[dict[str, Any]]:
    """Turn wide Open-Meteo payload rows into a pollutant-level forecast fact."""

    rows: list[dict[str, Any]] = []
    pollutant_fields = {
        "pm25": "pm2_5",
        "pm10": "pm10",
        "no2": "nitrogen_dioxide",
        "o3": "ozone",
        "so2": "sulphur_dioxide",
        "co": "carbon_monoxide",
    }
    for record in records:
        record_values = _serialize_dataclass(record)
        base_values = {
            key: value
            for key, value in record_values.items()
            if key not in pollutant_fields.values()
        }
        for pollutant, field_name in pollutant_fields.items():
            value = record_values[field_name]
            if value is None:
                continue
            rows.append(
                {
                    **base_values,
                    "pollutant": pollutant,
                    "value": value,
                    "unit": "µg/m³",
                }
            )
    return rows


def _serialize_dataclass(record: Any) -> dict[str, Any]:
    return asdict(record)
