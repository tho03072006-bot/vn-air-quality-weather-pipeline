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
from vn_air_quality_weather.geography import PROVINCES
from vn_air_quality_weather.loaders.duckdb_loader import load_incremental
from vn_air_quality_weather.models import (
    AirQualityForecastHourly,
    ModeledAirQualityHourly,
    ObservedAirQualityHourly,
    PipelineRunAudit,
    WeatherForecastHourly,
    WeatherHourly,
)

DEMO_DAY_COUNT = 3
DEFAULT_FORECAST_AGE_HOURS = 0
# Anchor positions whose newest forecast batch is deliberately incomplete, so the
# fixture reproduces the partial-refresh state that mixed-vintage tests need.
PARTIAL_POLLUTANT_ANCHOR_INDEX = 0
STALE_WEATHER_ANCHOR_INDEX = 1
# These two anchors make the contiguous-window contract discriminating. One has
# a single missing weather hour, so a model that sequences the remaining rows
# without checking timestamps will jump the gap. The other has one deliberately
# severe PM2.5 hour between otherwise much better hours, so averaging a window
# score can no longer look equivalent to taking its worst hour.
CONTIGUOUS_GAP_ANCHOR_INDEX = 2
CONTIGUOUS_GAP_LEAD_HOUR = 6
CONTIGUOUS_WORST_HOUR_ANCHOR_INDEX = 3
CONTIGUOUS_WORST_HOUR_LEAD_HOUR = 6
CONTIGUOUS_WORST_HOUR_PM25 = 80.0
FORECAST_VINTAGE_GAP_HOURS = 6
# Station id, name, sensors, and an offset in degrees from the city anchor.
#
# The offset is the point of the fixture, not decoration. A station that sat exactly
# on the anchor would make the representativeness term identically zero, and a
# decomposition whose spatial half is always zero cannot be told apart from one that
# forgot to compute it. Roughly two kilometres, which is the order of a real
# anchor-to-station distance and small enough to stay inside the same province.
OBSERVED_STATIONS = {
    "hanoi": (
        "1001",
        "Demo Hanoi station",
        {"pm25": 2001, "pm10": 2002, "o3": 2003},
        (0.018, -0.014),
    ),
    "ho_chi_minh": ("1002", "Demo HCMC station", {"pm25": 2004}, (-0.021, 0.011)),
}
# A second Hanoi station reporting a suspect PM2.5 value for one hour each day.
# It gives that city/hour grain both a flagged and an unflagged reading, which is
# the only case where excluding flagged data actually moves the published average.
# The all-flagged hours produced by FLAGGED_HOUR below merely make a grain vanish,
# so on their own they cannot tell a mart that excludes flagged readings apart
# from one that averages them in.
CO_LOCATED_STATION = ("1003", "Demo Hanoi roadside station", 2005, (0.006, 0.009))
CO_LOCATED_CITY_KEY = "hanoi"
CO_LOCATED_FLAGGED_HOUR = 10
FLAGGED_HOUR = 23


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
    forecast_age_hours: int = DEFAULT_FORECAST_AGE_HOURS,
) -> None:
    """Build the offline fixture.

    ``forecast_age_hours`` shifts both forecast vintages into the past without
    changing their relative six-hour spacing. The default keeps the normal happy
    path. A value greater than the 72-hour forecast span creates an exhausted
    horizon, which is the only state in which the serving mart has to fall back
    to a last-known row. Without it that fallback cannot be tested at all.
    """

    if forecast_age_hours < 0:
        raise ValueError("forecast_age_hours must be zero or greater")

    start_date = start_date or default_start_date()
    now = datetime.now(UTC)
    weather: list[WeatherHourly] = []
    modeled: list[ModeledAirQualityHourly] = []
    observed: list[ObservedAirQualityHourly] = []
    air_quality_forecasts: list[AirQualityForecastHourly] = []
    weather_forecasts: list[WeatherForecastHourly] = []
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

                if city.key == CO_LOCATED_CITY_KEY and hour == CO_LOCATED_FLAGGED_HOUR:
                    co_station_id, co_station_name, co_sensor_id, co_offset = CO_LOCATED_STATION
                    observed.append(
                        ObservedAirQualityHourly(
                            city_key=city.key,
                            station_id=co_station_id,
                            station_name=co_station_name,
                            sensor_id=co_sensor_id,
                            pollutant="pm25",
                            unit="µg/m³",
                            # Far from the healthy station on purpose, so that
                            # including it would visibly move the average instead
                            # of disappearing into rounding.
                            observed_at_utc=timestamp,
                            value=round(pm25 * 6.0, 2),
                            flagged=True,
                            station_latitude=round(city.latitude + co_offset[0], 6),
                            station_longitude=round(city.longitude + co_offset[1], 6),
                        )
                    )

                station = OBSERVED_STATIONS.get(city.key)
                if station is None:
                    continue
                station_id, station_name, sensors, offset = station
                station_latitude = round(city.latitude + offset[0], 6)
                station_longitude = round(city.longitude + offset[1], 6)
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
                            flagged=hour == FLAGGED_HOUR,
                            station_latitude=station_latitude,
                            station_longitude=station_longitude,
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
        # Per city: weather, CAMS and an OpenAQ locations call, plus one
        # sensor-hours call for each observed sensor.
        raw_objects = len(CITIES) * 3 + sum(
            len(sensors) for _, _, sensors, _ in OBSERVED_STATIONS.values()
        )
        # A synthetic reuse count, not a simulation of real behaviour: a real manual
        # replay reports zero reused, because the raw object key embeds the run_id.
        # A non-zero value here exists so the audit-consistency test exercises the
        # created + reused = attempted invariant with reused > 0, which an all-zero
        # fixture would never reach.
        raw_objects_reused = day_index
        runs.append(
            PipelineRunAudit(
                run_id=f"demo-{data_date.isoformat()}",
                data_date=data_date,
                started_at_utc=started_at,
                finished_at_utc=started_at + timedelta(minutes=4),
                duration_seconds=240.0,
                raw_backend="local",
                include_openaq=True,
                raw_objects=raw_objects,
                weather_rows=len(weather) - weather_before,
                observed_air_quality_rows=len(observed) - observed_before,
                modeled_air_quality_rows=modeled_measurements,
                pipeline_name="historical",
                status="SUCCESS",
                requested_location_count=len(CITIES),
                succeeded_location_count=len(CITIES),
                failed_location_count=0,
                raw_objects_created=raw_objects - raw_objects_reused,
                raw_objects_reused=raw_objects_reused,
            )
        )

    # Two vintages six hours apart, matching the forecast DAG cadence, so the
    # serving mart actually has to choose one. The newer vintage is deliberately
    # incomplete for two anchors: one loses ozone, the other loses its whole
    # weather series. That reproduces a partially refreshed batch, which is the
    # only state in which a serving row can straddle two model runs. Without it
    # the vintage tests would pass no matter how the mart resolved its vintage.
    #
    # Shifting the current vintage back by more than 72 hours creates a
    # deliberately exhausted horizon. That state must leave the serving mart with
    # one explicitly stale last-known row per location, not zero rows.
    forecast_issued_at = (now - timedelta(hours=forecast_age_hours)).replace(
        minute=0, second=0, microsecond=0
    )
    previous_issued_at = forecast_issued_at - timedelta(hours=FORECAST_VINTAGE_GAP_HOURS)

    for issued_at in (previous_issued_at, forecast_issued_at):
        is_current_vintage = issued_at == forecast_issued_at
        for province_index, province in enumerate(PROVINCES.values()):
            # Only the two anchors that exercise the mixed-vintage path carry the
            # older batch, so the fixture stays small enough to rebuild per run.
            carries_older_vintage = province_index in {
                PARTIAL_POLLUTANT_ANCHOR_INDEX,
                STALE_WEATHER_ANCHOR_INDEX,
            }
            if not is_current_vintage and not carries_older_vintage:
                continue
            drop_ozone = is_current_vintage and province_index == PARTIAL_POLLUTANT_ANCHOR_INDEX
            drop_weather = is_current_vintage and province_index == STALE_WEATHER_ANCHOR_INDEX

            for lead_hour in range(72):
                valid_at = issued_at + timedelta(hours=lead_hour)
                local_hour = (valid_at.hour + 7) % 24
                rush_hour_penalty = 12.0 if local_hour in {7, 8, 17, 18} else 0.0
                pm25 = 10.0 + province_index % 8 + rush_hour_penalty + abs(12 - local_hour) * 0.4
                if (
                    is_current_vintage
                    and province_index == CONTIGUOUS_WORST_HOUR_ANCHOR_INDEX
                    and lead_hour == CONTIGUOUS_WORST_HOUR_LEAD_HOUR
                ):
                    pm25 = CONTIGUOUS_WORST_HOUR_PM25
                rain_probability = 65.0 if local_hour in {15, 16, 17} else 15.0
                air_quality_forecasts.append(
                    AirQualityForecastHourly(
                        location_key=province.key,
                        province_code=province.code,
                        forecast_issued_at_utc=issued_at,
                        valid_at_utc=valid_at,
                        pm2_5=pm25,
                        pm10=pm25 * 1.45,
                        nitrogen_dioxide=8.0 + province_index % 5 + rush_hour_penalty * 0.3,
                        ozone=None if drop_ozone else 38.0 + max(local_hour - 8, 0) * 2.0,
                        sulphur_dioxide=3.0 + province_index % 3,
                        carbon_monoxide=180.0 + rush_hour_penalty * 4.0,
                        grid_latitude=province.latitude,
                        grid_longitude=province.longitude,
                    )
                )
                drop_contiguous_gap_hour = (
                    is_current_vintage
                    and province_index == CONTIGUOUS_GAP_ANCHOR_INDEX
                    and lead_hour == CONTIGUOUS_GAP_LEAD_HOUR
                )
                if drop_weather or drop_contiguous_gap_hour:
                    continue
                weather_forecasts.append(
                    WeatherForecastHourly(
                        location_key=province.key,
                        province_code=province.code,
                        forecast_issued_at_utc=issued_at,
                        valid_at_utc=valid_at,
                        temperature_2m=25.0 + province_index % 4 + max(6 - abs(13 - local_hour), 0),
                        apparent_temperature=27.0
                        + province_index % 4
                        + max(7 - abs(13 - local_hour), 0),
                        relative_humidity_2m=72.0 - max(8 - abs(13 - local_hour), 0),
                        precipitation_probability=rain_probability,
                        precipitation=1.2 if rain_probability >= 60 else 0.0,
                        wind_speed_10m=7.0 + lead_hour % 5,
                        wind_direction_10m=float((lead_hour * 15) % 360),
                        uv_index=max(0.0, 8.0 - abs(12 - local_hour) * 1.3),
                        grid_latitude=province.latitude,
                        grid_longitude=province.longitude,
                    )
                )

    # Audit rows for the two forecast vintages. These give the fixture a coherent
    # explanation for its own shape: the older vintage covers only two anchors
    # because that run partially failed, and a partially failed run is precisely
    # what leaves the warehouse able to serve one anchor from two model runs.
    # A location that returned a payload with a null pollutant still counts as
    # succeeded -- incomplete content is not a failed request.
    older_anchor_count = 2
    older_raw_objects = older_anchor_count * 2
    current_raw_objects = len(PROVINCES) * 2
    current_reused = 2
    runs.extend(
        [
            PipelineRunAudit(
                run_id=f"demo-forecast-{previous_issued_at:%Y%m%dT%H%M%SZ}",
                data_date=previous_issued_at.date(),
                started_at_utc=previous_issued_at,
                finished_at_utc=previous_issued_at + timedelta(minutes=6),
                duration_seconds=360.0,
                raw_backend="local",
                include_openaq=False,
                raw_objects=older_raw_objects,
                weather_rows=0,
                observed_air_quality_rows=0,
                modeled_air_quality_rows=0,
                pipeline_name="forecast",
                status="PARTIAL",
                requested_location_count=len(PROVINCES),
                succeeded_location_count=older_anchor_count,
                failed_location_count=len(PROVINCES) - older_anchor_count,
                raw_objects_created=older_raw_objects,
                raw_objects_reused=0,
                weather_forecast_rows=older_anchor_count * 72,
                air_quality_forecast_rows=older_anchor_count * 72 * 6,
                error_category="ReadTimeout",
                error_summary=(
                    f"{len(PROVINCES) - older_anchor_count} location(s) failed: "
                    "upstream read timeout"
                ),
            ),
            PipelineRunAudit(
                run_id=f"demo-forecast-{forecast_issued_at:%Y%m%dT%H%M%SZ}",
                data_date=forecast_issued_at.date(),
                started_at_utc=forecast_issued_at,
                finished_at_utc=forecast_issued_at + timedelta(minutes=5),
                duration_seconds=300.0,
                raw_backend="local",
                include_openaq=False,
                raw_objects=current_raw_objects,
                weather_rows=0,
                observed_air_quality_rows=0,
                modeled_air_quality_rows=0,
                pipeline_name="forecast",
                status="SUCCESS",
                requested_location_count=len(PROVINCES),
                succeeded_location_count=len(PROVINCES),
                failed_location_count=0,
                raw_objects_created=current_raw_objects - current_reused,
                raw_objects_reused=current_reused,
                weather_forecast_rows=(len(PROVINCES) - 1) * 72 - 1,
                air_quality_forecast_rows=len(PROVINCES) * 72 * 6 - 72,
            ),
        ]
    )

    summary = load_incremental(
        database_path=database_path,
        weather=weather,
        observed_air_quality=observed,
        modeled_air_quality=modeled,
        pipeline_runs=runs,
        weather_forecasts=weather_forecasts,
        air_quality_forecasts=air_quality_forecasts,
        pipeline_name="vn_air_quality_weather_demo",
    )
    print(
        f"demo warehouse={summary.database_path} weather={summary.weather_rows} "
        f"air_quality={summary.air_quality_rows} "
        f"weather_forecast={summary.weather_forecast_rows} "
        f"air_quality_forecast={summary.air_quality_forecast_rows} runs={len(runs)}"
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
    parser.add_argument(
        "--forecast-age-hours",
        type=int,
        default=DEFAULT_FORECAST_AGE_HOURS,
        help=(
            "Shift both forecast vintages this many hours into the past. "
            "Use a value above 72 to exercise an exhausted forecast horizon."
        ),
    )
    arguments = parser.parse_args()
    build_demo(
        arguments.database,
        arguments.start_date,
        arguments.days,
        arguments.forecast_age_hours,
    )


if __name__ == "__main__":
    main()
