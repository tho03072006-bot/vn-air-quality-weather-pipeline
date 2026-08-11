import math
from types import SimpleNamespace

import pytest

import dashboard.runtime as runtime
from dashboard.location_search import (
    SearchOption,
    filter_relevant_places,
    match_provinces,
    merge_search_results,
    normalise_place_name,
)
from vn_air_quality_weather.clients.geocoding import GeocodingResult

_MEAN_EARTH_RADIUS_KM = 6_371.0088


@pytest.fixture
def provinces() -> list[dict[str, object]]:
    return [
        {
            "province_key": "an_giang",
            "province_name": "An Giang",
            "anchor_name": "Rạch Giá",
            "latitude": 10.0125,
            "longitude": 105.0809,
        },
        {
            "province_key": "gia_lai",
            "province_name": "Gia Lai",
            "anchor_name": "Quy Nhơn",
            "latitude": 13.7820,
            "longitude": 109.2190,
        },
        {
            "province_key": "lao_cai",
            "province_name": "Lào Cai",
            "anchor_name": "Lào Cai",
            "latitude": 22.4856,
            "longitude": 103.9707,
        },
        {
            "province_key": "da_nang",
            "province_name": "Đà Nẵng",
            "anchor_name": "Đà Nẵng",
            "latitude": 16.0544,
            "longitude": 108.2022,
        },
    ]


def test_an_giang_registry_result_precedes_wrong_geocoding_result(
    provinces: list[dict[str, object]],
) -> None:
    geocoded = [
        _geocoded(
            "An Giang, Huyện Phù Mỹ, Gia Lai",
            latitude=14.3397,
            longitude=109.1000,
            feature_code="PPL",
        )
    ]

    without_registry = merge_search_results([], geocoded)
    assert without_registry[0].origin == "geocoding"

    province_matches = match_provinces(provinces, "An Giang")
    merged = merge_search_results(province_matches, geocoded)

    assert merged[0].label == "An Giang — điểm đại diện tỉnh (Rạch Giá)"
    assert merged[0].origin == "province_registry"
    assert merged[0].province_key == "an_giang"


@pytest.mark.parametrize("query", ["an giang", "AN GIANG"])
def test_an_giang_matching_is_case_insensitive(
    provinces: list[dict[str, object]], query: str
) -> None:
    matches = match_provinces(provinces, query)
    assert [match.province_key for match in matches] == ["an_giang"]


def test_place_name_normalisation_removes_vietnamese_accents_and_extra_spaces() -> None:
    assert normalise_place_name("  ĐÀ   NẴNG ") == "da nang"
    assert normalise_place_name("Lào Cai") == normalise_place_name("Lao Cai")


def test_prefix_matches_precede_substring_matches(
    provinces: list[dict[str, object]],
) -> None:
    matches = match_provinces(provinces, "la")
    assert [match.province_key for match in matches] == ["lao_cai", "gia_lai"]


def test_airports_are_filtered_while_populated_places_are_kept() -> None:
    geocoded = [
        _geocoded("Sân bay Phú Quốc", 10.1698, 103.9931, "AIRP"),
        _geocoded("Phú Quốc", 10.2899, 103.9840, "PPL"),
        _geocoded("Hội An", 15.8794, 108.3350, "PPLA"),
    ]

    merged = merge_search_results([], geocoded)

    assert [option.label for option in merged] == ["Phú Quốc", "Hội An"]


@pytest.mark.parametrize(
    ("distance_km", "expected_count"),
    [(5.0, 1), (50.0, 2)],
)
def test_geocoding_dedupe_checks_both_sides_of_anchor_distance(
    distance_km: float,
    expected_count: int,
) -> None:
    anchor = SearchOption(
        label="An Giang — điểm đại diện tỉnh (Rạch Giá)",
        name="An Giang",
        latitude=10.0125,
        longitude=105.0809,
        origin="province_registry",
        province_key="an_giang",
    )
    latitude = anchor.latitude + math.degrees(distance_km / _MEAN_EARTH_RADIUS_KM)
    geocoded = [_geocoded("Kết quả mở rộng", latitude, anchor.longitude, "PPL")]

    merged = merge_search_results([anchor], geocoded, dedupe_km=10.0)

    assert len(merged) == expected_count


def test_geocoding_is_not_deduplicated_against_an_unmatched_province() -> None:
    geocoded = [_geocoded("Rạch Giá", 10.0125, 105.0809, "PPLA")]
    assert merge_search_results([], geocoded) == geocoded


def test_query_with_no_match_returns_empty_without_raising(
    provinces: list[dict[str, object]],
) -> None:
    assert match_provinces(provinces, "Không Tồn Tại") == []
    assert merge_search_results([], []) == []


def test_merge_search_results_respects_limit(
    provinces: list[dict[str, object]],
) -> None:
    province_matches = match_provinces(provinces, "a")
    geocoded = [_geocoded("Hội An", 15.8794, 108.3350, "PPL")]

    merged = merge_search_results(province_matches, geocoded, limit=2)

    assert len(merged) == 2
    assert all(option.origin == "province_registry" for option in merged)


def test_registry_results_unchanged_when_five_or_fewer_match() -> None:
    provinces = [_province(index) for index in range(5)]

    matches = match_provinces(provinces, "Tỉnh")

    assert [option.province_key for option in matches] == [
        f"province_{index}" for index in range(5)
    ]


def test_registry_results_are_capped_at_five() -> None:
    provinces = [_province(index) for index in range(7)]

    matches = match_provinces(provinces, "Tỉnh")

    assert len(matches) == 5
    assert [option.province_key for option in matches] == [
        f"province_{index}" for index in range(5)
    ]


def test_registry_cap_preserves_prefix_before_substring_ranking() -> None:
    provinces = [_province(index, name=f"X Tỉnh {index}") for index in range(5)] + [
        _province(5, name="Tỉnh ưu tiên"),
        _province(6, name="Tỉnh tiếp theo"),
    ]

    matches = match_provinces(provinces, "Tỉnh")

    assert [option.province_key for option in matches[:2]] == ["province_5", "province_6"]


def test_relevance_uses_raw_provider_name_instead_of_display_label() -> None:
    misleading_label = _geocoded(
        "Địa danh khác, Quảng Nam, Hội An",
        15.8794,
        108.3350,
        "PPL",
        name="Địa danh khác",
    )

    assert filter_relevant_places([misleading_label], "Hội An") == []


def test_hoi_an_relevance_keeps_places_and_removes_measured_noise() -> None:
    options = [
        _geocoded(
            "Hội An, Thành Phố Hội An, Đà Nẵng",
            15.8794,
            108.3350,
            "PPL",
            name="Hội An",
        ),
        _geocoded(
            "Thôn Hội Yên, Huyện Hải Lăng, Quảng Trị",
            16.7333,
            107.3000,
            "PPL",
            name="Thôn Hội Yên",
        ),
        _geocoded(
            "Hội An, Huyện Đức Phổ, Quảng Ngãi",
            14.9000,
            108.9500,
            "PPL",
            name="Hội An",
        ),
        _geocoded(
            "Hội An, Huyện Tiên Phước, Đà Nẵng",
            15.5000,
            108.2833,
            "PPL",
            name="Hội An",
        ),
        _geocoded(
            "Cái Tầu Thượng, Huyện Chợ Mới, An Giang",
            10.4333,
            105.5500,
            "PPL",
            name="Cái Tầu Thượng",
        ),
        _geocoded(
            "Hội An Đông, Huyện Lấp Vò, Đồng Tháp",
            10.4000,
            105.5333,
            "PPL",
            name="Hội An Đông",
        ),
        _geocoded(
            "Hội An Thượng, Việt Nam",
            15.6000,
            108.3000,
            "PPL",
            name="Hội An Thượng",
        ),
    ]

    filtered = filter_relevant_places(options, "Hội An")

    assert [option.name for option in filtered] == [
        "Hội An",
        "Hội An",
        "Hội An",
        "Hội An Đông",
        "Hội An Thượng",
    ]
    assert {option.name for option in options if option not in filtered} == {
        "Thôn Hội Yên",
        "Cái Tầu Thượng",
    }


@pytest.mark.parametrize(
    ("name", "query"),
    [("Hội An", "hoi an"), ("Hội An", "Phố cổ Hội An")],
)
def test_relevance_accepts_normalized_name_containment_in_either_direction(
    name: str,
    query: str,
) -> None:
    option = _geocoded(name, 15.8794, 108.3350, "PPL", name=name)

    assert filter_relevant_places([option], query) == [option]


def test_relevance_rejects_empty_query() -> None:
    assert filter_relevant_places([_geocoded("Hội An", 15.8794, 108.3350, "PPL")], "  ") == []


def test_runtime_preserves_raw_name_and_labels_nearest_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = GeocodingResult(
        geonames_id=1,
        name="Hội An",
        latitude=15.8794,
        longitude=108.3350,
        timezone="Asia/Ho_Chi_Minh",
        country_code="VN",
        admin1="Quảng Nam",
        feature_code="PPLA",
    )

    class FakeGeocodingClient:
        def __init__(self, **_: object) -> None:
            pass

        def __enter__(self) -> "FakeGeocodingClient":
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def search(self, query: str, *, count: int) -> tuple[GeocodingResult, ...]:
            assert query == "Hội An"
            assert count == 10
            return (result,)

    monkeypatch.setattr(runtime, "OpenMeteoGeocodingClient", FakeGeocodingClient)
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: SimpleNamespace(
            http_timeout_seconds=1.0,
            retry_policy=lambda: object(),
        ),
    )

    (option,) = runtime.cached_location_search.__wrapped__("Hội An", 10)

    assert option.name == "Hội An"
    assert option.nearest_province_key == "da_nang"
    assert option.nearest_province_km == 24.1
    assert option.label == "Hội An, Quảng Nam · gần điểm đại diện Đà Nẵng (~24 km)"
    assert "thuộc" not in option.label.casefold()


def _geocoded(
    label: str,
    latitude: float,
    longitude: float,
    feature_code: str,
    *,
    name: str | None = None,
) -> SearchOption:
    return SearchOption(
        label=label,
        name=name or label,
        latitude=latitude,
        longitude=longitude,
        origin="geocoding",
        province_key=None,
        feature_code=feature_code,
    )


def _province(index: int, *, name: str | None = None) -> dict[str, object]:
    return {
        "province_key": f"province_{index}",
        "province_name": name or f"Tỉnh {index}",
        "anchor_name": f"Điểm {index}",
        "latitude": 10.0 + index * 0.1,
        "longitude": 105.0 + index * 0.1,
    }
