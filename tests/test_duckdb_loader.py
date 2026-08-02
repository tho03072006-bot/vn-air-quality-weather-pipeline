from datetime import UTC, datetime
from pathlib import Path

import duckdb

from vn_air_quality_weather.loaders.duckdb_loader import load_incremental
from vn_air_quality_weather.models import ModeledAirQualityHourly, WeatherHourly


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
