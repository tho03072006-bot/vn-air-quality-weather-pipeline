from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from vn_air_quality_weather.cities import City
from vn_air_quality_weather.models import ObservedAirQualityHourly
from vn_air_quality_weather.retry import DEFAULT_RETRY_POLICY, RetryPolicy, request_with_retry

OPENAQ_BASE_URL = "https://api.openaq.org/v3"
TARGET_POLLUTANTS = frozenset({"pm25", "pm10", "no2", "o3"})


@dataclass(frozen=True, slots=True)
class SensorSelection:
    city_key: str
    station_id: str
    station_name: str
    sensor_id: int
    pollutant: str
    # Where the station actually is. OpenAQ returns this on every location and this
    # class discarded it for the project's whole life, so the only record of a
    # station's position was the raw archive nobody queried.
    #
    # It is the missing half of finding O. The published model-station gap contains a
    # spatial term -- a province grid cell is not a street corner -- and separating
    # that term from the model's own bias requires sampling the model at the station's
    # coordinates. Without these two numbers in the warehouse there is nothing to
    # sample at.
    #
    # Optional because a location without coordinates is a real payload OpenAQ can
    # return, and dropping the sensor over it would trade observations for tidiness.
    latitude: float | None = None
    longitude: float | None = None


class OpenAQClient:
    """Small OpenAQ v3 client with bounded pagination and explicit time windows."""

    def __init__(
        self,
        api_key: str,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAQ API key must not be empty")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._headers = {"X-API-Key": api_key}
        self._retry_policy = retry_policy

    def fetch_locations(self, city: City, radius_meters: int = 25_000) -> dict[str, Any]:
        response = request_with_retry(
            self._client,
            "GET",
            f"{OPENAQ_BASE_URL}/locations",
            policy=self._retry_policy,
            headers=self._headers,
            params={
                "coordinates": f"{city.latitude},{city.longitude}",
                "radius": radius_meters,
                "limit": 100,
                "page": 1,
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("OpenAQ returned a non-object locations payload")
        return payload

    def fetch_sensor_hours(
        self,
        sensor_id: int,
        interval_start_utc: datetime,
        interval_end_utc: datetime,
        *,
        max_pages: int = 20,
    ) -> dict[str, Any]:
        if interval_start_utc.tzinfo is None or interval_end_utc.tzinfo is None:
            raise ValueError("OpenAQ intervals must be timezone-aware")
        if interval_start_utc >= interval_end_utc:
            raise ValueError("OpenAQ interval start must be before interval end")

        results: list[dict[str, Any]] = []
        last_meta: dict[str, Any] = {}
        limit = 1000

        for page in range(1, max_pages + 1):
            response = request_with_retry(
                self._client,
                "GET",
                f"{OPENAQ_BASE_URL}/sensors/{sensor_id}/hours",
                policy=self._retry_policy,
                headers=self._headers,
                params={
                    "datetime_from": _iso_z(interval_start_utc),
                    "datetime_to": _iso_z(interval_end_utc),
                    "limit": limit,
                    "page": page,
                },
            )
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError("OpenAQ returned a non-object sensor-hours payload")

            page_results = payload.get("results")
            if not isinstance(page_results, list):
                raise ValueError("OpenAQ sensor-hours payload is missing results")
            results.extend(item for item in page_results if isinstance(item, dict))

            meta = payload.get("meta")
            last_meta = dict(meta) if isinstance(meta, Mapping) else {}
            if len(page_results) < limit:
                break
        else:
            raise RuntimeError(f"OpenAQ pagination exceeded {max_pages} pages")

        return {"meta": {**last_meta, "returned": len(results)}, "results": results}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OpenAQClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def select_city_sensors(
    city_key: str,
    locations_payload: Mapping[str, Any],
    pollutants: frozenset[str] = TARGET_POLLUTANTS,
) -> dict[str, SensorSelection]:
    """Choose the most recently reporting sensor for each requested pollutant."""

    locations = locations_payload.get("results")
    if not isinstance(locations, list):
        raise ValueError("OpenAQ locations payload is missing results")

    ordered = sorted(
        (location for location in locations if isinstance(location, Mapping)),
        key=lambda location: str(
            (location.get("datetimeLast") or {}).get("utc", "")
            if isinstance(location.get("datetimeLast"), Mapping)
            else ""
        ),
        reverse=True,
    )

    selected: dict[str, SensorSelection] = {}
    for location in ordered:
        station_id = location.get("id")
        station_name = str(location.get("name") or station_id or "unknown")
        sensors = location.get("sensors")
        if station_id is None or not isinstance(sensors, list):
            continue

        for sensor in sensors:
            if not isinstance(sensor, Mapping):
                continue
            parameter = sensor.get("parameter")
            if not isinstance(parameter, Mapping):
                continue
            pollutant = str(parameter.get("name") or "").lower()
            sensor_id = sensor.get("id")
            if pollutant not in pollutants or pollutant in selected or sensor_id is None:
                continue
            latitude, longitude = _coordinates(location)
            selected[pollutant] = SensorSelection(
                city_key=city_key,
                station_id=str(station_id),
                station_name=station_name,
                sensor_id=int(sensor_id),
                pollutant=pollutant,
                latitude=latitude,
                longitude=longitude,
            )

    return selected


def _coordinates(location: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """Read a location's position, tolerating every shape that is not two numbers.

    Returns a pair of Nones rather than raising. A station whose coordinates are
    missing or malformed is still a station reporting real measurements, and refusing
    its observations would cost data to protect a column that only one downstream
    model needs.
    """

    coordinates = location.get("coordinates")
    if not isinstance(coordinates, Mapping):
        return None, None
    try:
        return float(coordinates["latitude"]), float(coordinates["longitude"])
    except (KeyError, TypeError, ValueError):
        return None, None


def normalize_sensor_hours(
    selection: SensorSelection,
    payload: Mapping[str, Any],
) -> list[ObservedAirQualityHourly]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("OpenAQ sensor-hours payload is missing results")

    records: list[ObservedAirQualityHourly] = []
    for result in results:
        if not isinstance(result, Mapping) or result.get("value") is None:
            continue
        parameter = result.get("parameter")
        period = result.get("period")
        flag_info = result.get("flagInfo")
        if not isinstance(parameter, Mapping) or not isinstance(period, Mapping):
            raise ValueError("OpenAQ hourly result is missing parameter or period")
        datetime_from = period.get("datetimeFrom")
        if not isinstance(datetime_from, Mapping) or not datetime_from.get("utc"):
            raise ValueError("OpenAQ hourly result is missing period.datetimeFrom.utc")

        pollutant = str(parameter.get("name") or selection.pollutant).lower()
        records.append(
            ObservedAirQualityHourly(
                city_key=selection.city_key,
                station_id=selection.station_id,
                station_name=selection.station_name,
                sensor_id=selection.sensor_id,
                pollutant=pollutant,
                unit=str(parameter.get("units") or "unknown"),
                observed_at_utc=_parse_utc(datetime_from["utc"]),
                value=float(result["value"]),
                flagged=bool(
                    flag_info.get("hasFlags", False) if isinstance(flag_info, Mapping) else False
                ),
                station_latitude=selection.latitude,
                station_longitude=selection.longitude,
            )
        )
    return records


def _parse_utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
