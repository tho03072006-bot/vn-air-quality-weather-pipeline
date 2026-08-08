import httpx
import pytest

from vn_air_quality_weather.clients.geocoding import (
    GEOCODING_URL,
    OpenMeteoGeocodingClient,
)


def test_geocoding_search_is_restricted_to_vietnam_and_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(GEOCODING_URL)
        assert request.url.params["name"] == "Hội An"
        assert request.url.params["count"] == "5"
        assert request.url.params["language"] == "vi"
        assert request.url.params["countryCode"] == "VN"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 1580541,
                        "name": "Hội An",
                        "latitude": 15.87944,
                        "longitude": 108.335,
                        "country_code": "VN",
                        "country": "Việt Nam",
                        "admin1": "Đà Nẵng",
                        "admin2": "Hội An",
                        "timezone": "Asia/Ho_Chi_Minh",
                        "feature_code": "PPLA2",
                    },
                    {
                        "id": 1,
                        "name": "Outside",
                        "latitude": 1.0,
                        "longitude": 1.0,
                        "country_code": "ZZ",
                    },
                    {"name": "Malformed"},
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenMeteoGeocodingClient(http_client=http_client)
        results = client.search(" Hội An ", count=5)

    assert len(results) == 1
    assert results[0].geonames_id == 1580541
    assert results[0].display_label == "Hội An, Đà Nẵng"
    assert results[0].timezone == "Asia/Ho_Chi_Minh"


def test_geocoding_empty_results_and_invalid_arguments() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    ) as http_client:
        client = OpenMeteoGeocodingClient(http_client=http_client)
        assert client.search("Sa Pa") == ()

        with pytest.raises(ValueError, match="two characters"):
            client.search("A")
        with pytest.raises(ValueError, match="between 1 and 100"):
            client.search("Sa Pa", count=101)
        with pytest.raises(ValueError, match="two-letter"):
            client.search("Sa Pa", country_code="Vietnam")


def test_geocoding_rejects_non_object_payload() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
    ) as http_client:
        client = OpenMeteoGeocodingClient(http_client=http_client)
        with pytest.raises(ValueError, match="non-object"):
            client.search("Hanoi")
