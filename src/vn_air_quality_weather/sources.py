"""Pure metadata registry for every external data source used by the project.

Licence metadata intentionally remains conservative until a human has verified
the current upstream terms.  Keeping this module free of I/O makes it safe to
use from pipelines, documentation tooling, and tests.
"""

from dataclasses import dataclass

MEASUREMENT_KINDS = frozenset(
    {
        "observed_station",
        "model_reanalysis",
        "model_forecast",
        "satellite_column",
    }
)


@dataclass(frozen=True, slots=True)
class DataSource:
    key: str
    display_name: str
    provider: str
    measurement_kind: str
    licence: str
    licence_url: str | None
    redistribution_allowed: bool | None
    attribution_required: bool
    notes: str


def _unverified_source(
    *,
    key: str,
    display_name: str,
    provider: str,
    measurement_kind: str,
    notes: str,
) -> DataSource:
    """Build a source with deliberately unverified licence metadata."""
    return DataSource(
        key=key,
        display_name=display_name,
        provider=provider,
        measurement_kind=measurement_kind,
        licence="UNVERIFIED",
        licence_url=None,
        redistribution_allowed=None,
        # Conservative operational default, not a statement about licence terms.
        attribution_required=True,
        notes=(
            f"{notes} Licence, attribution, and redistribution terms require "
            "human confirmation before external publication."
        ),
    )


SOURCES: dict[str, DataSource] = {
    "openaq": _unverified_source(
        key="openaq",
        display_name="OpenAQ API v3",
        provider="OpenAQ",
        measurement_kind="observed_station",
        notes="Observed measurements reported by monitoring stations.",
    ),
    "open_meteo_cams": _unverified_source(
        key="open_meteo_cams",
        display_name="Open-Meteo Air Quality API (CAMS)",
        provider="Open-Meteo / Copernicus Atmosphere Monitoring Service",
        measurement_kind="model_forecast",
        notes=(
            "Modeled air-quality fields used for spatial coverage and the forecast "
            "workflow; they must remain distinguishable from station observations."
        ),
    ),
    "open_meteo_weather": _unverified_source(
        key="open_meteo_weather",
        display_name="Open-Meteo Weather APIs",
        provider="Open-Meteo",
        measurement_kind="model_forecast",
        notes="Modeled hourly weather fields used by historical and forecast views.",
    ),
    "open_meteo_geocoding": _unverified_source(
        key="open_meteo_geocoding",
        display_name="Open-Meteo Geocoding API",
        provider="Open-Meteo / GeoNames",
        measurement_kind="model_forecast",
        notes=(
            "Reference metadata service rather than a measurement source; it is "
            "classified by the forecast workflow it supports because this registry "
            "does not define a lookup-only measurement kind."
        ),
    ),
}


def get_source(key: str) -> DataSource:
    """Return one registered source, raising ``KeyError`` for unknown keys."""
    return SOURCES[key]


def sources_by_kind(measurement_kind: str) -> list[DataSource]:
    """Return sources of one supported kind in stable registry order."""
    if measurement_kind not in MEASUREMENT_KINDS:
        raise ValueError(f"Unsupported measurement kind: {measurement_kind}")
    return [source for source in SOURCES.values() if source.measurement_kind == measurement_kind]
