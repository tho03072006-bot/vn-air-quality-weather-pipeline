"""Open-Meteo geocoding client for user-selected Vietnam locations."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from vn_air_quality_weather.retry import DEFAULT_RETRY_POLICY, RetryPolicy, request_with_retry

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


@dataclass(frozen=True, slots=True)
class GeocodingResult:
    """One location returned by Open-Meteo's GeoNames-backed search."""

    geonames_id: int
    name: str
    latitude: float
    longitude: float
    timezone: str
    country_code: str
    country: str | None = None
    admin1: str | None = None
    admin2: str | None = None
    admin3: str | None = None
    admin4: str | None = None
    feature_code: str | None = None

    @property
    def display_label(self) -> str:
        """Return a readable label without repeating administrative names."""

        parts: list[str] = []
        for value in (self.name, self.admin4, self.admin3, self.admin2, self.admin1):
            if value and value.casefold() not in {part.casefold() for part in parts}:
                parts.append(value)
        return ", ".join(parts)


class OpenMeteoGeocodingClient:
    """Small retrying client for the public Open-Meteo geocoding endpoint."""

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 20.0,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._retry_policy = retry_policy

    def search(
        self,
        name: str,
        *,
        count: int = 10,
        language: str = "vi",
        country_code: str = "VN",
    ) -> tuple[GeocodingResult, ...]:
        """Search locations, restricted to one ISO alpha-2 country by default."""

        query = name.strip()
        if len(query) < 2:
            raise ValueError("location search requires at least two characters")
        if not 1 <= count <= 100:
            raise ValueError("count must be between 1 and 100")

        normalized_language = language.strip().lower()
        normalized_country = country_code.strip().upper()
        if not normalized_language:
            raise ValueError("language must not be empty")
        if len(normalized_country) != 2 or not normalized_country.isalpha():
            raise ValueError("country_code must be a two-letter ISO code")

        response = request_with_retry(
            self._client,
            "GET",
            GEOCODING_URL,
            policy=self._retry_policy,
            params={
                "name": query,
                "count": count,
                "language": normalized_language,
                "format": "json",
                "countryCode": normalized_country,
            },
        )
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("Open-Meteo returned a non-object geocoding payload")
        rows = payload.get("results", [])
        if rows is None:
            return ()
        if not isinstance(rows, list):
            raise ValueError("Open-Meteo geocoding results must be a list")

        results: list[GeocodingResult] = []
        for row in rows:
            parsed = _parse_result(row, normalized_country)
            if parsed is not None:
                results.append(parsed)
        return tuple(results)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OpenMeteoGeocodingClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _parse_result(row: Any, country_code: str) -> GeocodingResult | None:
    if not isinstance(row, Mapping):
        return None
    row_country = str(row.get("country_code", "")).upper()
    if row_country != country_code:
        return None
    try:
        return GeocodingResult(
            geonames_id=int(row["id"]),
            name=str(row["name"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            timezone=str(row.get("timezone") or "Asia/Ho_Chi_Minh"),
            country_code=row_country,
            country=_optional_text(row.get("country")),
            admin1=_optional_text(row.get("admin1")),
            admin2=_optional_text(row.get("admin2")),
            admin3=_optional_text(row.get("admin3")),
            admin4=_optional_text(row.get("admin4")),
            feature_code=_optional_text(row.get("feature_code")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
