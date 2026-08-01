from datetime import UTC, datetime

import httpx

from vn_air_quality_weather.cities import CITIES
from vn_air_quality_weather.clients.open_aq import (
    OPENAQ_BASE_URL,
    OpenAQClient,
    SensorSelection,
    normalize_sensor_hours,
    select_city_sensors,
)


def test_client_sends_key_and_paginates_hours() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["X-API-Key"] == "test-key"
        return httpx.Response(
            200,
            json={"meta": {"page": 1}, "results": [{"value": 12.0}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenAQClient("test-key", http_client=http_client)
        payload = client.fetch_sensor_hours(
            123,
            datetime(2026, 7, 27, tzinfo=UTC),
            datetime(2026, 7, 28, tzinfo=UTC),
        )

    assert requests[0].url.path == "/v3/sensors/123/hours"
    assert requests[0].url.params["limit"] == "1000"
    assert payload["meta"]["returned"] == 1


def test_locations_request_uses_city_radius() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(f"{OPENAQ_BASE_URL}/locations")
        assert request.url.params["coordinates"] == "21.0278,105.8342"
        assert request.url.params["radius"] == "25000"
        return httpx.Response(200, json={"meta": {"found": 0}, "results": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenAQClient("test-key", http_client=http_client)
        assert client.fetch_locations(CITIES["hanoi"])["results"] == []


def test_selects_latest_sensor_for_each_pollutant() -> None:
    payload = {
        "results": [
            {
                "id": 2,
                "name": "new",
                "datetimeLast": {"utc": "2026-07-27T23:00:00Z"},
                "sensors": [
                    {"id": 20, "parameter": {"name": "pm25"}},
                    {"id": 21, "parameter": {"name": "o3"}},
                ],
            },
            {
                "id": 1,
                "name": "old",
                "datetimeLast": {"utc": "2025-01-01T00:00:00Z"},
                "sensors": [{"id": 10, "parameter": {"name": "pm25"}}],
            },
        ]
    }
    selected = select_city_sensors("hanoi", payload)
    assert selected["pm25"].sensor_id == 20
    assert selected["o3"].station_name == "new"


def test_normalizes_sensor_hours() -> None:
    selection = SensorSelection("hanoi", "2", "station", 20, "pm25")
    payload = {
        "results": [
            {
                "value": 15.1,
                "flagInfo": {"hasFlags": False},
                "parameter": {"name": "pm25", "units": "µg/m³"},
                "period": {"datetimeFrom": {"utc": "2026-07-27T00:00:00Z"}},
            }
        ]
    }
    records = normalize_sensor_hours(selection, payload)
    assert records[0].value == 15.1
    assert records[0].observed_at_utc == datetime(2026, 7, 27, tzinfo=UTC)
    assert records[0].source_type == "observed"
