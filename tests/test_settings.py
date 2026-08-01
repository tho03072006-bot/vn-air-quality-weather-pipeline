from vn_air_quality_weather.cities import CITIES


def test_expected_cities_are_configured() -> None:
    assert set(CITIES) == {"hanoi", "ho_chi_minh", "da_nang"}


def test_city_coordinates_are_valid() -> None:
    for city in CITIES.values():
        assert -90 <= city.latitude <= 90
        assert -180 <= city.longitude <= 180
        assert city.timezone == "Asia/Ho_Chi_Minh"
