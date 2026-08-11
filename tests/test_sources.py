import re
from dataclasses import FrozenInstanceError

import pytest

from vn_air_quality_weather.sources import (
    MEASUREMENT_KINDS,
    SOURCES,
    DataSource,
    get_source,
    sources_by_kind,
)

EXPECTED_KEYS = {
    "openaq",
    "open_meteo_cams",
    "open_meteo_weather",
    "open_meteo_geocoding",
}


def test_registry_contains_exactly_the_expected_sources() -> None:
    assert set(SOURCES) == EXPECTED_KEYS
    assert all(source.key == key for key, source in SOURCES.items())


@pytest.mark.parametrize("source", SOURCES.values(), ids=lambda source: source.key)
def test_source_metadata_is_well_formed_and_conservatively_unverified(
    source: DataSource,
) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9_]*", source.key)
    assert source.measurement_kind in MEASUREMENT_KINDS
    assert source.display_name
    assert source.provider
    assert source.licence == "UNVERIFIED"
    assert source.licence_url is None
    assert source.attribution_required is True
    assert source.redistribution_allowed is None
    assert "human confirmation" in source.notes


def test_data_source_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        SOURCES["openaq"].display_name = "changed"  # type: ignore[misc]


def test_get_source_returns_registered_instance() -> None:
    assert get_source("openaq") is SOURCES["openaq"]


def test_get_source_rejects_unknown_key() -> None:
    with pytest.raises(KeyError):
        get_source("unknown")


def test_sources_by_kind_filters_in_registry_order() -> None:
    assert [source.key for source in sources_by_kind("model_forecast")] == [
        "open_meteo_cams",
        "open_meteo_weather",
        "open_meteo_geocoding",
    ]


def test_sources_by_valid_kind_can_be_empty() -> None:
    assert sources_by_kind("satellite_column") == []


def test_sources_by_kind_rejects_unsupported_kind() -> None:
    with pytest.raises(ValueError, match="Unsupported measurement kind"):
        sources_by_kind("lookup")
