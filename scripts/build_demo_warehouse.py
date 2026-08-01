import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from vn_air_quality_weather.cities import CITIES
from vn_air_quality_weather.loaders.duckdb_loader import load_incremental
from vn_air_quality_weather.models import ModeledAirQualityHourly, WeatherHourly


def build_demo(database_path: Path, data_date: date = date(2026, 7, 27)) -> None:
    weather: list[WeatherHourly] = []
    air_quality: list[ModeledAirQualityHourly] = []
    start = datetime.combine(data_date, datetime.min.time(), tzinfo=UTC)

    for city_index, city in enumerate(CITIES.values()):
        for hour in range(24):
            timestamp = start + timedelta(hours=hour)
            rain = 1.5 if hour in {6, 7, 18} else 0.0
            weather.append(
                WeatherHourly(
                    city_key=city.key,
                    observed_at_utc=timestamp,
                    temperature_2m=26.0 + city_index + hour * 0.12,
                    relative_humidity_2m=68.0 + ((hour + city_index) % 12),
                    precipitation=rain,
                    wind_speed_10m=6.0 + (hour % 6),
                    wind_direction_10m=float((hour * 15) % 360),
                    grid_latitude=city.latitude,
                    grid_longitude=city.longitude,
                )
            )
            pm25 = 12.0 + city_index * 5 + abs(12 - hour) * 0.7 - rain * 1.5
            air_quality.append(
                ModeledAirQualityHourly(
                    city_key=city.key,
                    observed_at_utc=timestamp,
                    pm2_5=max(pm25, 0.0),
                    pm10=max(pm25 * 1.45, 0.0),
                    nitrogen_dioxide=5.0 + city_index + hour * 0.1,
                    ozone=42.0 + hour * 0.8,
                    grid_latitude=city.latitude,
                    grid_longitude=city.longitude,
                )
            )

    summary = load_incremental(
        database_path=database_path,
        weather=weather,
        observed_air_quality=[],
        modeled_air_quality=air_quality,
        pipeline_name="vn_air_quality_weather_demo",
    )
    print(
        f"demo warehouse={summary.database_path} weather={summary.weather_rows} "
        f"air_quality={summary.air_quality_rows}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args()
    build_demo(arguments.database)


if __name__ == "__main__":
    main()
