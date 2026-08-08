from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import httpx

from vn_air_quality_weather.cities import City
from vn_air_quality_weather.models import (
    AirQualityForecastHourly,
    ModeledAirQualityHourly,
    WeatherForecastHourly,
    WeatherHourly,
)
from vn_air_quality_weather.retry import DEFAULT_RETRY_POLICY, RetryPolicy, request_with_retry

AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WEATHER_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
WEATHER_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_VARIABLES = (
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "ozone",
)
WEATHER_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
)
AIR_QUALITY_FORECAST_VARIABLES = (
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "ozone",
    "sulphur_dioxide",
    "carbon_monoxide",
)
WEATHER_FORECAST_VARIABLES = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation_probability",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "uv_index",
)


class OpenMeteoClient:
    def __init__(
        self,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._retry_policy = retry_policy

    def fetch_modeled_air_quality(
        self,
        city: City,
        data_date: date,
    ) -> dict[str, Any]:
        return self._get_json(
            AIR_QUALITY_URL,
            params={
                "latitude": city.latitude,
                "longitude": city.longitude,
                "hourly": ",".join(AIR_QUALITY_VARIABLES),
                "start_date": data_date.isoformat(),
                "end_date": data_date.isoformat(),
                "timezone": "GMT",
            },
            label="air-quality",
        )

    def fetch_weather(self, city: City, data_date: date) -> dict[str, Any]:
        return self._get_json(
            WEATHER_URL,
            params={
                "latitude": city.latitude,
                "longitude": city.longitude,
                "hourly": ",".join(WEATHER_VARIABLES),
                "start_date": data_date.isoformat(),
                "end_date": data_date.isoformat(),
                "timezone": "GMT",
            },
            label="weather",
        )

    def fetch_air_quality_forecast(
        self,
        location: City,
        forecast_hours: int = 72,
    ) -> dict[str, Any]:
        _validate_forecast_hours(forecast_hours)
        return self._get_json(
            AIR_QUALITY_URL,
            params={
                "latitude": location.latitude,
                "longitude": location.longitude,
                "hourly": ",".join(AIR_QUALITY_FORECAST_VARIABLES),
                "forecast_hours": forecast_hours,
                "timezone": "GMT",
            },
            label="air-quality forecast",
        )

    def fetch_weather_forecast(
        self,
        location: City,
        forecast_hours: int = 72,
    ) -> dict[str, Any]:
        _validate_forecast_hours(forecast_hours)
        return self._get_json(
            WEATHER_FORECAST_URL,
            params={
                "latitude": location.latitude,
                "longitude": location.longitude,
                "hourly": ",".join(WEATHER_FORECAST_VARIABLES),
                "forecast_hours": forecast_hours,
                "timezone": "GMT",
            },
            label="weather forecast",
        )

    def _get_json(self, url: str, *, params: dict[str, Any], label: str) -> dict[str, Any]:
        response = request_with_retry(
            self._client,
            "GET",
            url,
            policy=self._retry_policy,
            params=params,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Open-Meteo returned a non-object {label} payload")
        return payload

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OpenMeteoClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def normalize_modeled_air_quality(
    city_key: str,
    payload: Mapping[str, Any],
) -> list[ModeledAirQualityHourly]:
    hourly = _validated_hourly(payload, AIR_QUALITY_VARIABLES, "air-quality")
    times = hourly["time"]
    grid_latitude = float(payload["latitude"])
    grid_longitude = float(payload["longitude"])

    records: list[ModeledAirQualityHourly] = []
    for index, timestamp in enumerate(times):
        records.append(
            ModeledAirQualityHourly(
                city_key=city_key,
                observed_at_utc=_utc_datetime(timestamp),
                pm2_5=_optional_float(hourly["pm2_5"][index]),
                pm10=_optional_float(hourly["pm10"][index]),
                nitrogen_dioxide=_optional_float(hourly["nitrogen_dioxide"][index]),
                ozone=_optional_float(hourly["ozone"][index]),
                grid_latitude=grid_latitude,
                grid_longitude=grid_longitude,
            )
        )

    return records


def normalize_weather(city_key: str, payload: Mapping[str, Any]) -> list[WeatherHourly]:
    hourly = _validated_hourly(payload, WEATHER_VARIABLES, "weather")
    times = hourly["time"]
    grid_latitude = float(payload["latitude"])
    grid_longitude = float(payload["longitude"])

    records: list[WeatherHourly] = []
    for index, timestamp in enumerate(times):
        records.append(
            WeatherHourly(
                city_key=city_key,
                observed_at_utc=_utc_datetime(timestamp),
                temperature_2m=_optional_float(hourly["temperature_2m"][index]),
                relative_humidity_2m=_optional_float(hourly["relative_humidity_2m"][index]),
                precipitation=_optional_float(hourly["precipitation"][index]),
                wind_speed_10m=_optional_float(hourly["wind_speed_10m"][index]),
                wind_direction_10m=_optional_float(hourly["wind_direction_10m"][index]),
                grid_latitude=grid_latitude,
                grid_longitude=grid_longitude,
            )
        )
    return records


def normalize_air_quality_forecast(
    location_key: str,
    province_code: str,
    forecast_issued_at_utc: datetime,
    payload: Mapping[str, Any],
) -> list[AirQualityForecastHourly]:
    hourly = _validated_hourly(
        payload,
        AIR_QUALITY_FORECAST_VARIABLES,
        "air-quality forecast",
    )
    issued_at = _require_aware_utc(forecast_issued_at_utc)
    grid_latitude = float(payload["latitude"])
    grid_longitude = float(payload["longitude"])

    return [
        AirQualityForecastHourly(
            location_key=location_key,
            province_code=province_code,
            forecast_issued_at_utc=issued_at,
            valid_at_utc=_utc_datetime(timestamp),
            pm2_5=_optional_float(hourly["pm2_5"][index]),
            pm10=_optional_float(hourly["pm10"][index]),
            nitrogen_dioxide=_optional_float(hourly["nitrogen_dioxide"][index]),
            ozone=_optional_float(hourly["ozone"][index]),
            sulphur_dioxide=_optional_float(hourly["sulphur_dioxide"][index]),
            carbon_monoxide=_optional_float(hourly["carbon_monoxide"][index]),
            grid_latitude=grid_latitude,
            grid_longitude=grid_longitude,
        )
        for index, timestamp in enumerate(hourly["time"])
    ]


def normalize_weather_forecast(
    location_key: str,
    province_code: str,
    forecast_issued_at_utc: datetime,
    payload: Mapping[str, Any],
) -> list[WeatherForecastHourly]:
    hourly = _validated_hourly(payload, WEATHER_FORECAST_VARIABLES, "weather forecast")
    issued_at = _require_aware_utc(forecast_issued_at_utc)
    grid_latitude = float(payload["latitude"])
    grid_longitude = float(payload["longitude"])

    return [
        WeatherForecastHourly(
            location_key=location_key,
            province_code=province_code,
            forecast_issued_at_utc=issued_at,
            valid_at_utc=_utc_datetime(timestamp),
            temperature_2m=_optional_float(hourly["temperature_2m"][index]),
            apparent_temperature=_optional_float(hourly["apparent_temperature"][index]),
            relative_humidity_2m=_optional_float(hourly["relative_humidity_2m"][index]),
            precipitation_probability=_optional_float(hourly["precipitation_probability"][index]),
            precipitation=_optional_float(hourly["precipitation"][index]),
            wind_speed_10m=_optional_float(hourly["wind_speed_10m"][index]),
            wind_direction_10m=_optional_float(hourly["wind_direction_10m"][index]),
            uv_index=_optional_float(hourly["uv_index"][index]),
            grid_latitude=grid_latitude,
            grid_longitude=grid_longitude,
        )
        for index, timestamp in enumerate(hourly["time"])
    ]


def _validated_hourly(
    payload: Mapping[str, Any], variables: tuple[str, ...], label: str
) -> dict[str, list[Any]]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, Mapping):
        raise ValueError(f"Open-Meteo payload is missing hourly {label} data")

    times = hourly.get("time")
    if not isinstance(times, list):
        raise ValueError("Open-Meteo payload is missing hourly timestamps")

    validated: dict[str, list[Any]] = {"time": times}
    for variable in variables:
        values = hourly.get(variable)
        if not isinstance(values, list):
            raise ValueError(f"Open-Meteo payload is missing {variable}")
        if len(values) != len(times):
            raise ValueError(f"Open-Meteo {variable} length does not match time length")
        validated[variable] = values
    return validated


def _utc_datetime(value: Any) -> datetime:
    observed_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if observed_at.tzinfo is None:
        return observed_at.replace(tzinfo=UTC)
    return observed_at.astimezone(UTC)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _validate_forecast_hours(value: int) -> None:
    if not 1 <= value <= 168:
        raise ValueError("forecast_hours must be between 1 and 168")


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("forecast_issued_at_utc must be timezone-aware")
    return value.astimezone(UTC)
