from datetime import UTC, date, datetime

import httpx
import pytest

from vn_air_quality_weather.cities import CITIES
from vn_air_quality_weather.clients.open_meteo import (
    AIR_QUALITY_URL,
    OpenMeteoClient,
    normalize_modeled_air_quality,
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
