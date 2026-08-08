import pytest

from vn_air_quality_weather.forecast_pipeline import selected_provinces


def test_forecast_scope_defaults_to_core_locations() -> None:
    assert [province.key for province in selected_provinces(None, False)] == [
        "hanoi",
        "ho_chi_minh",
        "da_nang",
    ]


def test_all_provinces_scope_is_complete() -> None:
    provinces = selected_provinces(None, True)
    assert len(provinces) == 34
    assert {province.code for province in provinces} >= {"01", "48", "79", "96"}


def test_explicit_forecast_scope_is_validated() -> None:
    assert [province.key for province in selected_provinces(["hue"], False)] == ["hue"]

    with pytest.raises(ValueError, match="not both"):
        selected_provinces(["hue"], True)
    with pytest.raises(ValueError, match="unknown province"):
        selected_provinces(["unknown"], False)
