import pytest

from vn_air_quality_weather.cities import CITIES
from vn_air_quality_weather.geography import (
    CORE_LOCATION_KEYS,
    PROVINCES,
    haversine_km,
    nearest_province,
    province_by_code,
    province_cities,
    validate_vietnam_coordinates,
)


def test_current_province_registry_is_complete_and_unique() -> None:
    assert len(PROVINCES) == 34
    assert len({province.code for province in PROVINCES.values()}) == 34
    assert sum(province.unit_type == "municipality" for province in PROVINCES.values()) == 6


def test_core_locations_remain_backward_compatible() -> None:
    assert set(CITIES) == set(CORE_LOCATION_KEYS)
    for key, city in CITIES.items():
        province = PROVINCES[key]
        assert city.latitude == province.latitude
        assert city.longitude == province.longitude
        assert city.timezone == province.timezone


def test_province_lookup_and_selection() -> None:
    assert province_by_code("48").key == "da_nang"
    assert [location.key for location in province_cities(["hanoi", "ca_mau"])] == [
        "hanoi",
        "ca_mau",
    ]

    with pytest.raises(ValueError, match="unknown province"):
        province_cities(["not_a_province"])


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(21.0278, 105.8342), (9.1769, 105.15), (8.65, 111.92)],
)
def test_supported_vietnam_coordinates(latitude: float, longitude: float) -> None:
    validate_vietnam_coordinates(latitude, longitude)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(6.9, 105.0), (25.0, 105.0), (10.0, 117.0)],
)
def test_coordinates_outside_service_envelope_are_rejected(
    latitude: float, longitude: float
) -> None:
    with pytest.raises(ValueError, match="outside"):
        validate_vietnam_coordinates(latitude, longitude)


def test_nearest_province_is_a_distance_reference_only() -> None:
    province, distance_km = nearest_province(16.0544, 108.2022)
    assert province.key == "da_nang"
    assert distance_km == 0.0
    assert haversine_km(21.0278, 105.8342, 21.0278, 105.8342) == 0.0
