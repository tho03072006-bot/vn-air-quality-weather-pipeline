"""Build a deterministic offline warehouse so CI can run dbt without any API.

The fixture deliberately covers three UTC days and both source types. The AQI
models need a multi-day, dense hourly series to exercise the Nowcast window and
the rolling 8-hour ozone mean, and they need at least one observed station so
the observed/modeled split is tested rather than assumed.
"""

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from vn_air_quality_weather.cities import CITIES
from vn_air_quality_weather.loaders.duckdb_loader import load_incremental
from vn_air_quality_weather.models import (
    ModeledAirQualityHourly,
    ObservedAirQualityHourly,
    PipelineRunAudit,
    WeatherHourly,
)

DEMO_DAY_COUNT = 3
OBSERVED_STATIONS = {
    "hanoi": ("1001", "Demo Hanoi station", {"pm25": 2001, "pm10": 2002, "o3": 2003}),
    "ho_chi_minh": ("1002", "Demo HCMC station", {"pm25": 2004}),
}


def default_start_date(today: date | None = None) -> date:
    """Anchor the fixture just behind today.

    Every measurement then sits in the past, which assert_no_future_timestamps
    requires, and the newest audit row stays inside the source-freshness
    window. Measurement values depend only on the day index, never on the
    absolute date, so the warehouse contents stay deterministic even though the
    timestamps move.
    """

    return (today or datetime.now(UTC).date()) - timedelta(days=DEMO_DAY_COUNT)


def build_demo(
    database_path: Path,
    start_date: date | None = None,
    day_count: int = DEMO_DAY_COUNT,
) -> None:
    start_date = start_date or default_start_date()
    now = datetime.now(UTC)
    weather: list[WeatherHourly] = []
    modeled: list[ModeledAirQualityHourly] = []
    observed: list[ObservedAirQualityHourly] = []
    runs: list[PipelineRunAudit] = []

    for day_index in range(day_count):
        data_date = start_date + timedelta(days=day_index)
        start = datetime.combine(data_date, datetime.min.time(), tzinfo=UTC)
        # Always in the past, and the newest run finishes about a day ago so the
        # source-freshness warn threshold is not tripped by the fixture itself.
        started_at = now - timedelta(days=day_count - day_index, minutes=10)
        weather_before = len(weather)
        modeled_before = len(modeled)
        observed_before = len(observed)

        for city_index, city in enumerate(CITIES.values()):
            for hour in range(24):
                timestamp = start + timedelta(hours=hour)
                rain = 1.5 if hour in {6, 7, 18} else 0.0
                weather.append(
                    WeatherHourly(
                        city_key=city.key,
                        observed_at_utc=timestamp,
                        temperature_2m=26.0 + city_index + hour * 0.12 + day_index,
                        relative_humidity_2m=68.0 + ((hour + city_index) % 12),
                        precipitation=rain,
                        wind_speed_10m=6.0 + (hour % 6),
                        wind_direction_10m=float((hour * 15) % 360),
                        grid_latitude=city.latitude,
                        grid_longitude=city.longitude,
                    )
                )

                pm25 = max(
                    12.0 + city_index * 5 + abs(12 - hour) * 0.7 - rain * 1.5 + day_index * 3,
                    0.0,
                )
                modeled.append(
                    ModeledAirQualityHourly(
                        city_key=city.key,
                        observed_at_utc=timestamp,
                        pm2_5=pm25,
                        pm10=pm25 * 1.45,
                        nitrogen_dioxide=5.0 + city_index + hour * 0.1,
                        ozone=42.0 + hour * 0.8 + day_index * 4,
                        grid_latitude=city.latitude,
                        grid_longitude=city.longitude,
                    )
                )

                station = OBSERVED_STATIONS.get(city.key)
                if station is None:
                    continue
                station_id, station_name, sensors = station
                for pollutant, sensor_id in sensors.items():
                    # A deliberate gap so the Nowcast spine has a hole to handle.
                    if pollutant == "pm25" and hour in {3, 4}:
                        continue
                    observed.append(
                        ObservedAirQualityHourly(
                            city_key=city.key,
                            station_id=station_id,
                            station_name=station_name,
                            sensor_id=sensor_id,
                            pollutant=pollutant,
                            unit="µg/m³",
                            observed_at_utc=timestamp,
                            value=_observed_value(pollutant, pm25, hour),
                            flagged=hour == 23,
                        )
                    )

        # Counts are derived from what was actually generated, so the audit
        # table and the measurement tables cannot drift apart.
        modeled_measurements = sum(
            1
            for record in modeled[modeled_before:]
            for value in (record.pm2_5, record.pm10, record.nitrogen_dioxide, record.ozone)
            if value is not None
        )
        runs.append(
            PipelineRunAudit(
                run_id=f"demo-{data_date.isoformat()}",
                data_date=data_date,
                started_at_utc=started_at,
                finished_at_utc=started_at + timedelta(minutes=4),
                duration_seconds=240.0,
                raw_backend="local",
                include_openaq=True,
                # Per city: weather, CAMS and an OpenAQ locations call, plus one
                # sensor-hours call for each observed sensor.
                raw_objects=len(CITIES) * 3
                + sum(len(sensors) for _, _, sensors in OBSERVED_STATIONS.values()),
                weather_rows=len(weather) - weather_before,
                observed_air_quality_rows=len(observed) - observed_before,
                modeled_air_quality_rows=modeled_measurements,
            )
        )

    summary = load_incremental(
        database_path=database_path,
        weather=weather,
        observed_air_quality=observed,
        modeled_air_quality=modeled,
        pipeline_runs=runs,
        pipeline_name="vn_air_quality_weather_demo",
    )
    print(
        f"demo warehouse={summary.database_path} weather={summary.weather_rows} "
        f"air_quality={summary.air_quality_rows} runs={len(runs)}"
    )


def _observed_value(pollutant: str, pm25: float, hour: int) -> float:
    if pollutant == "pm25":
        return round(pm25 * 1.1, 2)
    if pollutant == "pm10":
        return round(pm25 * 1.6, 2)
    return round(38.0 + hour * 0.9, 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, default=None)
    parser.add_argument("--days", type=int, default=DEMO_DAY_COUNT)
    arguments = parser.parse_args()
    build_demo(arguments.database, arguments.start_date, arguments.days)


if __name__ == "__main__":
    main()
