"""Pin the assumption the forecast-versus-analysis decomposition rests on.

`fct_forecast_vs_analysis` subtracts two numbers and calls the result forecast drift.
That is only true if both numbers describe the same point in space. They come from two
registries that have no connection to each other:

* forecasts are fetched at the province anchor, which reaches the pipeline through
  `dbt/seeds/provinces_2025.csv` and `dim_location`;
* historical model values are fetched at `CITIES[key]`, a Python constant.

They agree today, verified by measurement. Nothing makes them agree. Move an anchor by
a kilometre in the seed and the drift term silently acquires a spatial component --
the SQL keeps working, every dbt test keeps passing, and the number quietly starts
answering a different question. That is the failure this file exists to prevent, and
it is why the assertion lives here rather than in a comment.

No warehouse required: both sides are files in the repository.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from vn_air_quality_weather.cities import CITIES

SEED = Path(__file__).resolve().parents[1] / "dbt" / "seeds" / "provinces_2025.csv"


def _seed_anchors() -> dict[str, tuple[float, float]]:
    with SEED.open(encoding="utf-8") as handle:
        return {
            row["province_key"]: (float(row["latitude"]), float(row["longitude"]))
            for row in csv.DictReader(handle)
        }


@pytest.mark.parametrize("city_key", sorted(CITIES))
def test_city_coordinates_match_the_province_anchor(city_key: str) -> None:
    """The two registries must describe the same point, exactly.

    Exact equality rather than a tolerance, deliberately. A tolerance would need a
    defensible size, and there is no distance at which a silent spatial term becomes
    acceptable -- Open-Meteo snaps a request to its own grid, so two coordinates that
    differ at all may or may not land in the same cell, and "may or may not" is not a
    property a measurement can be built on.
    """

    anchors = _seed_anchors()
    assert city_key in anchors, (
        f"{city_key} is in CITIES but not in the province seed, so its historical "
        "model series has no anchor to be compared against"
    )

    city = CITIES[city_key]
    assert (city.latitude, city.longitude) == anchors[city_key], (
        f"{city_key}: CITIES has ({city.latitude}, {city.longitude}) while the seed "
        f"anchor is {anchors[city_key]}. fct_forecast_vs_analysis subtracts a forecast "
        "at the seed anchor from an analysis at the CITIES point and calls the result "
        "forecast drift; with the two points apart that difference also contains "
        "distance, and nothing downstream can tell the two apart."
    )
