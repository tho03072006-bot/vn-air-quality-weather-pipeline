from datetime import UTC, datetime
from pathlib import Path

import duckdb

from vn_air_quality_weather.loaders.duckdb_loader import load_incremental
from vn_air_quality_weather.models import (
    AirQualityForecastHourly,
    ModeledAirQualityHourly,
    WeatherForecastHourly,
    WeatherHourly,
)


def test_rerun_does_not_duplicate_natural_keys(tmp_path: Path, monkeypatch) -> None:
    # dlt adds several nested package names, so keep its state outside the
    # already descriptive test directory to stay below Windows MAX_PATH.
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path.parent / "dlt-state"))
    database_path = tmp_path / "warehouse.duckdb"
    weather = [
        WeatherHourly(
            city_key="hanoi",
            observed_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
            temperature_2m=29.0,
            relative_humidity_2m=70.0,
            precipitation=0.0,
            wind_speed_10m=8.0,
            wind_direction_10m=180.0,
            grid_latitude=21.0,
            grid_longitude=105.8,
        )
    ]
    modeled = [
        ModeledAirQualityHourly(
            city_key="hanoi",
            observed_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
            pm2_5=12.0,
            pm10=20.0,
            nitrogen_dioxide=4.0,
            ozone=50.0,
            grid_latitude=21.0,
            grid_longitude=105.8,
        )
    ]

    arguments = {
        "database_path": database_path,
        "weather": weather,
        "observed_air_quality": [],
        "modeled_air_quality": modeled,
        "pipeline_name": f"test_pipeline_{tmp_path.name.replace('-', '_')}",
    }
    load_incremental(**arguments)
    load_incremental(**arguments)

    with duckdb.connect(str(database_path), read_only=True) as connection:
        weather_count = connection.execute("select count(*) from raw.weather_hourly").fetchone()
        air_count = connection.execute("select count(*) from raw.air_quality_hourly").fetchone()
    assert weather_count == (1,)
    assert air_count == (4,)


def test_forecast_vintages_are_idempotent_but_preserved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path.parent / "dlt-forecast-state"))
    database_path = tmp_path / "forecast.duckdb"
    first_issue = datetime(2026, 7, 27, tzinfo=UTC)
    second_issue = datetime(2026, 7, 27, 6, tzinfo=UTC)
    valid_at = datetime(2026, 7, 28, tzinfo=UTC)

    def load(issue_time: datetime, pm25: float) -> None:
        load_incremental(
            database_path=database_path,
            weather=[],
            observed_air_quality=[],
            modeled_air_quality=[],
            air_quality_forecasts=[
                AirQualityForecastHourly(
                    location_key="hanoi",
                    province_code="01",
                    forecast_issued_at_utc=issue_time,
                    valid_at_utc=valid_at,
                    pm2_5=pm25,
                    pm10=20.0,
                    nitrogen_dioxide=4.0,
                    ozone=50.0,
                    sulphur_dioxide=2.0,
                    carbon_monoxide=180.0,
                    grid_latitude=21.0,
                    grid_longitude=105.8,
                )
            ],
            weather_forecasts=[
                WeatherForecastHourly(
                    location_key="hanoi",
                    province_code="01",
                    forecast_issued_at_utc=issue_time,
                    valid_at_utc=valid_at,
                    temperature_2m=29.0,
                    apparent_temperature=31.0,
                    relative_humidity_2m=70.0,
                    precipitation_probability=10.0,
                    precipitation=0.0,
                    wind_speed_10m=8.0,
                    wind_direction_10m=180.0,
                    uv_index=0.0,
                    grid_latitude=21.0,
                    grid_longitude=105.8,
                )
            ],
            pipeline_name=f"forecast_{tmp_path.name.replace('-', '_')}",
        )

    load(first_issue, 12.0)
    load(first_issue, 12.0)
    load(second_issue, 15.0)

    with duckdb.connect(str(database_path), read_only=True) as connection:
        air_count = connection.execute(
            "select count(*) from raw.air_quality_forecast_hourly"
        ).fetchone()
        weather_count = connection.execute(
            "select count(*) from raw.weather_forecast_hourly"
        ).fetchone()

    assert air_count == (12,)
    assert weather_count == (2,)
