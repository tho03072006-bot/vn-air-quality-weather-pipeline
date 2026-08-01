from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import httpx

from vn_air_quality_weather.cities import City
from vn_air_quality_weather.models import ModeledAirQualityHourly

AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
AIR_QUALITY_VARIABLES = (
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "ozone",
)


class OpenMeteoClient:
    def __init__(
        self,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def fetch_modeled_air_quality(
        self,
        city: City,
        data_date: date,
    ) -> dict[str, Any]:
        response = self._client.get(
            AIR_QUALITY_URL,
            params={
                "latitude": city.latitude,
                "longitude": city.longitude,
                "hourly": ",".join(AIR_QUALITY_VARIABLES),
                "start_date": data_date.isoformat(),
                "end_date": data_date.isoformat(),
                "timezone": "GMT",
            },
        )
        response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Open-Meteo returned a non-object JSON payload")

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
    hourly = payload.get("hourly")
    if not isinstance(hourly, Mapping):
        raise ValueError("Open-Meteo payload is missing hourly data")

    times = hourly.get("time")
    if not isinstance(times, list):
        raise ValueError("Open-Meteo payload is missing hourly timestamps")

    series = {variable: hourly.get(variable) for variable in AIR_QUALITY_VARIABLES}

    for variable, values in series.items():
        if not isinstance(values, list):
            raise ValueError(f"Open-Meteo payload is missing {variable}")
        if len(values) != len(times):
            raise ValueError(f"Open-Meteo {variable} length does not match time length")

    grid_latitude = float(payload["latitude"])
    grid_longitude = float(payload["longitude"])

    records: list[ModeledAirQualityHourly] = []

    for index, timestamp in enumerate(times):
        observed_at = datetime.fromisoformat(str(timestamp))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        else:
            observed_at = observed_at.astimezone(UTC)

        records.append(
            ModeledAirQualityHourly(
                city_key=city_key,
                observed_at_utc=observed_at,
                pm2_5=_optional_float(series["pm2_5"][index]),
                pm10=_optional_float(series["pm10"][index]),
                nitrogen_dioxide=_optional_float(series["nitrogen_dioxide"][index]),
                ozone=_optional_float(series["ozone"][index]),
                grid_latitude=grid_latitude,
                grid_longitude=grid_longitude,
            )
        )

    return records


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
