from datetime import UTC, date, datetime

import httpx
import pytest

from vn_air_quality_weather.cities import CITIES
from vn_air_quality_weather.clients.open_meteo import (
    AIR_QUALITY_FORECAST_VARIABLES,
    AIR_QUALITY_URL,
    WEATHER_FORECAST_URL,
    WEATHER_URL,
    OpenMeteoClient,
    normalize_air_quality_forecast,
    normalize_modeled_air_quality,
    normalize_weather,
    normalize_weather_forecast,
)

SAMPLE_PAYLOAD = {
    "latitude": 16.1,
    "longitude": 108.2,
    "timezone": "GMT",
    "hourly": {
        "time": [
            "2026-07-27T00:00",
            "2026-07-27T01:00",
        ],
        "pm2_5": [12.5, 13.0],
        "pm10": [20.0, 21.0],
        "nitrogen_dioxide": [4.1, None],
        "ozone": [55.0, 56.0],
    },
}

WEATHER_PAYLOAD = {
    "latitude": 21.0,
    "longitude": 105.8,
    "timezone": "GMT",
    "hourly": {
        "time": ["2026-07-27T00:00", "2026-07-27T01:00"],
        "temperature_2m": [29.0, 28.5],
        "relative_humidity_2m": [70.0, 72.0],
        "precipitation": [0.0, 1.2],
        "wind_speed_10m": [8.0, 7.5],
        "wind_direction_10m": [180.0, 190.0],
    },
}

FORECAST_AIR_PAYLOAD = {
    **SAMPLE_PAYLOAD,
    "hourly": {
        **SAMPLE_PAYLOAD["hourly"],
        "sulphur_dioxide": [2.0, 2.1],
        "carbon_monoxide": [180.0, 181.0],
    },
}

FORECAST_WEATHER_PAYLOAD = {
    **WEATHER_PAYLOAD,
    "hourly": {
        **WEATHER_PAYLOAD["hourly"],
        "apparent_temperature": [31.0, 30.5],
        "precipitation_probability": [10.0, 60.0],
        "uv_index": [0.0, 0.2],
    },
}


def test_client_requests_one_day_in_gmt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(AIR_QUALITY_URL)
        assert request.url.params["start_date"] == "2026-07-27"
        assert request.url.params["end_date"] == "2026-07-27"
        assert request.url.params["timezone"] == "GMT"

        return httpx.Response(200, json=SAMPLE_PAYLOAD)

    transport = httpx.MockTransport(handler)

    with httpx.Client(transport=transport) as http_client:
        client = OpenMeteoClient(http_client=http_client)
        payload = client.fetch_modeled_air_quality(
            CITIES["da_nang"],
            date(2026, 7, 27),
        )

    assert payload == SAMPLE_PAYLOAD


def test_normalize_modeled_air_quality() -> None:
    records = normalize_modeled_air_quality(
        city_key="da_nang",
        payload=SAMPLE_PAYLOAD,
    )

    assert len(records) == 2
    assert records[0].city_key == "da_nang"
    assert records[0].observed_at_utc == datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
    assert records[0].pm2_5 == 12.5
    assert records[1].nitrogen_dioxide is None
    assert records[0].source_type == "modeled"
    assert records[0].source_name == "open_meteo_cams"


def test_normalizer_rejects_misaligned_arrays() -> None:
    invalid_payload = {
        **SAMPLE_PAYLOAD,
        "hourly": {
            **SAMPLE_PAYLOAD["hourly"],
            "pm2_5": [12.5],
        },
    }

    with pytest.raises(ValueError, match="pm2_5 length"):
        normalize_modeled_air_quality(
            city_key="da_nang",
            payload=invalid_payload,
        )


def test_weather_client_and_normalizer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(WEATHER_URL)
        assert request.url.params["timezone"] == "GMT"
        return httpx.Response(200, json=WEATHER_PAYLOAD)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenMeteoClient(http_client=http_client)
        payload = client.fetch_weather(CITIES["hanoi"], date(2026, 7, 27))

    records = normalize_weather("hanoi", payload)
    assert len(records) == 2
    assert records[0].temperature_2m == 29.0
    assert records[1].precipitation == 1.2
    assert records[0].observed_at_utc == datetime(2026, 7, 27, tzinfo=UTC)


def test_forecast_clients_request_bounded_gmt_hours() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["forecast_hours"] == "48"
        assert request.url.params["timezone"] == "GMT"
        if str(request.url).startswith(WEATHER_FORECAST_URL):
            return httpx.Response(200, json=FORECAST_WEATHER_PAYLOAD)
        assert request.url.params["hourly"] == ",".join(AIR_QUALITY_FORECAST_VARIABLES)
        return httpx.Response(200, json=FORECAST_AIR_PAYLOAD)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenMeteoClient(http_client=http_client)
        location = CITIES["hanoi"]
        assert client.fetch_air_quality_forecast(location, 48) == FORECAST_AIR_PAYLOAD
        assert client.fetch_weather_forecast(location, 48) == FORECAST_WEATHER_PAYLOAD

    with pytest.raises(ValueError, match="between 1 and 168"):
        client.fetch_weather_forecast(CITIES["hanoi"], 0)


def test_normalize_forecast_vintages() -> None:
    issued_at = datetime(2026, 7, 26, 18, tzinfo=UTC)

    air_records = normalize_air_quality_forecast("da_nang", "48", issued_at, FORECAST_AIR_PAYLOAD)
    weather_records = normalize_weather_forecast(
        "da_nang", "48", issued_at, FORECAST_WEATHER_PAYLOAD
    )

    assert len(air_records) == 2
    assert air_records[0].forecast_issued_at_utc == issued_at
    assert air_records[0].province_code == "48"
    assert air_records[0].sulphur_dioxide == 2.0
    assert air_records[0].source_type == "modeled"
    assert weather_records[1].precipitation_probability == 60.0
    assert weather_records[1].apparent_temperature == 30.5


def test_forecast_normalizer_requires_aware_issue_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize_air_quality_forecast(
            "hanoi",
            "01",
            datetime(2026, 7, 26, 18),
            FORECAST_AIR_PAYLOAD,
        )
