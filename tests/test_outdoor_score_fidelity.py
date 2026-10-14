"""Pin the Python outdoor heuristic to the dbt model it says it mirrors.

`on_demand.score_outdoor_conditions` is a hand-written copy of the scoring in
`mart_location_hourly_forecast`. Its own tests assert hand-written expected values,
which is enough to catch a typo in the copy and nothing at all to catch the copy and
the original drifting apart -- and they had. dbt renders the rain term as `10.0%`,
because `round(x, 0)` on a double returns a double and `concat` spells it with the
decimal; the Python wrote `10%`. Every province page said one thing and the
custom-location page said another about the same reading, and every test was green.

This compares against the warehouse instead. Two thousand published hours span clean
air and 165 µg/m³, missing readings, both temperature branches and the caps at either
end, so a constant changed in dbt fails here on the next run rather than surfacing
later on a page nobody was watching.

Skipped when the configured warehouse is absent, in the same way the DAG tests skip
without an Airflow runtime. A skipped pin holds nothing, so CI must run this where the
warehouse exists.
"""

from __future__ import annotations

import duckdb
import pytest

from vn_air_quality_weather.on_demand import score_outdoor_conditions
from vn_air_quality_weather.settings import Settings

# Two engines computing the same double: a tenth is far tighter than any difference
# that would matter to a reader and far looser than the last bit of a float.
TOLERANCE = 0.05


@pytest.fixture(scope="module")
def published_hours() -> list[tuple]:
    path = Settings().duckdb_path
    if not path.exists():
        pytest.skip(f"no warehouse at {path}; this pin needs one to hold anything")
    with duckdb.connect(str(path), read_only=True) as connection:
        rows = connection.execute(
            """
            select
                pm25_ugm3, precipitation_probability_pct, apparent_temperature_c,
                temperature_2m_c, uv_index,
                outdoor_score, decision_label, decision_explanation
            from analytics.mart_location_hourly_forecast
            """
        ).fetchall()
    if not rows:
        pytest.skip("the forecast mart is empty; run the pipeline before pinning to it")
    return rows


def test_the_python_mirror_reproduces_every_published_score(published_hours: list[tuple]) -> None:
    mismatches = []
    for pm25, rain, apparent, temperature, uv, score, label, _ in published_hours:
        got_score, got_label, _ = score_outdoor_conditions(
            pm25_ugm3=pm25,
            precipitation_probability_pct=rain,
            apparent_temperature_c=apparent,
            temperature_2m_c=temperature,
            uv_index=uv,
        )
        if abs(got_score - score) > TOLERANCE or got_label != label:
            mismatches.append(
                f"pm25={pm25} rain={rain} apparent={apparent} uv={uv}: "
                f"warehouse=({score}, {label}) python=({got_score}, {got_label})"
            )
    assert not mismatches, mismatches[:5]


def test_the_python_mirror_writes_the_same_sentence(published_hours: list[tuple]) -> None:
    """The regression this file was written for.

    The sentence is shown verbatim beside the warehouse's own, so a difference here is
    two spellings of one reading on one screen.
    """

    mismatches = []
    for pm25, rain, apparent, temperature, uv, _, _, explanation in published_hours:
        _, _, got = score_outdoor_conditions(
            pm25_ugm3=pm25,
            precipitation_probability_pct=rain,
            apparent_temperature_c=apparent,
            temperature_2m_c=temperature,
            uv_index=uv,
        )
        if got != explanation:
            mismatches.append(f"warehouse=[{explanation}] python=[{got}]")
    assert not mismatches, mismatches[:5]


def test_the_sample_actually_exercises_the_formula(published_hours: list[tuple]) -> None:
    """A pin over a narrow slice pins nothing.

    Asserting that the published hours reach both temperature branches, a UV penalty
    and more than one verdict is what makes a green run mean the constants were
    exercised rather than merely not contradicted.
    """

    apparent = [row[2] for row in published_hours if row[2] is not None]
    uv = [row[4] for row in published_hours if row[4] is not None]
    labels = {row[6] for row in published_hours}

    assert any(value > 36.0 for value in apparent) or any(value < 15.0 for value in apparent), (
        "no published hour is hot or cold enough to charge a temperature penalty"
    )
    assert any(value > 5.0 for value in uv), "no published hour has UV above the free threshold"
    assert len(labels) > 1, "every published hour carries the same verdict"
