"""Pure ranking and deduplication for custom-location search results."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Literal

SearchOrigin = Literal["province_registry", "geocoding"]

# Airports identify transport infrastructure rather than the community whose
# outdoor air-quality decision the user is making, so AIRP is deliberately absent.
ALLOWED_FEATURE_CODES: frozenset[str] = frozenset({"PPL", "PPLA", "PPLA2", "PPLC", "ISL"})

_MEAN_EARTH_RADIUS_KM = 6_371.0088


@dataclass(frozen=True, slots=True)
class SearchOption:
    """One selectable location from the registry or geocoding provider."""

    label: str
    name: str
    latitude: float
    longitude: float
    origin: SearchOrigin
    province_key: str | None
    feature_code: str | None = None
    nearest_province_key: str | None = None
    nearest_province_km: float | None = None


def normalise_place_name(text: str) -> str:
    """Return a case-insensitive, accent-free place name with stable spacing."""

    casefolded = text.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFKD", casefolded)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_accents.split())


def match_provinces(
    provinces: list[dict[str, object]],
    query: str,
) -> list[SearchOption]:
    """Match registry province and anchor names, preferring prefix matches."""

    normalized_query = normalise_place_name(query)
    if not normalized_query:
        return []

    ranked: list[tuple[int, int, SearchOption]] = []
    for index, province in enumerate(provinces):
        province_name = str(province["province_name"])
        anchor_name = str(province["anchor_name"])
        candidate_names = (
            normalise_place_name(province_name),
            normalise_place_name(anchor_name),
        )
        if any(name.startswith(normalized_query) for name in candidate_names):
            rank = 0
        elif any(normalized_query in name for name in candidate_names):
            rank = 1
        else:
            continue

        ranked.append(
            (
                rank,
                index,
                SearchOption(
                    label=f"{province_name} — điểm đại diện tỉnh ({anchor_name})",
                    name=province_name,
                    latitude=float(province["latitude"]),
                    longitude=float(province["longitude"]),
                    origin="province_registry",
                    province_key=str(province["province_key"]),
                ),
            )
        )

    ranked.sort(key=lambda item: (item[0], item[1]))
    # Leave at least half of the default ten-result UI budget for geocoding.
    return [option for _, _, option in ranked[:5]]


def filter_relevant_places(
    options: list[SearchOption],
    query: str,
) -> list[SearchOption]:
    """Drop geocoding hits whose provider name does not match the submitted query."""

    normalized_query = normalise_place_name(query)
    if not normalized_query:
        return []

    relevant: list[SearchOption] = []
    for option in options:
        if option.origin != "geocoding":
            relevant.append(option)
            continue
        normalized_name = normalise_place_name(option.name)
        if normalized_name and (
            normalized_query in normalized_name or normalized_name in normalized_query
        ):
            relevant.append(option)
    return relevant


def merge_search_results(
    province_matches: list[SearchOption],
    geocoded: list[SearchOption],
    *,
    limit: int = 10,
    dedupe_km: float = 10.0,
) -> list[SearchOption]:
    """Put registry matches first and remove geocoding duplicates near their anchors."""

    if limit <= 0:
        return []

    merged = list(province_matches)
    for option in geocoded:
        feature_code = (option.feature_code or "").upper()
        if option.origin != "geocoding" or feature_code not in ALLOWED_FEATURE_CODES:
            continue
        if any(_distance_km(option, anchor) <= dedupe_km for anchor in province_matches):
            continue
        merged.append(option)

    return merged[:limit]


def _distance_km(first: SearchOption, second: SearchOption) -> float:
    first_latitude = math.radians(first.latitude)
    second_latitude = math.radians(second.latitude)
    latitude_delta = second_latitude - first_latitude
    longitude_delta = math.radians(second.longitude - first.longitude)

    haversine = math.sin(latitude_delta / 2) ** 2 + (
        math.cos(first_latitude) * math.cos(second_latitude) * math.sin(longitude_delta / 2) ** 2
    )
    angular_distance = 2 * math.asin(min(1.0, math.sqrt(haversine)))
    return _MEAN_EARTH_RADIUS_KM * angular_distance
