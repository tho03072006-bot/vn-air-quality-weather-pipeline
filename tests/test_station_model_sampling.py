"""Cover the step that decides where the model gets sampled.

The representativeness term in `mart_station_representativeness` is only meaningful if
the model was fetched at the station's actual position. Nothing downstream can check
that: by the time a coordinate reaches the mart it is just a number, and a wrong one
produces a plausible answer to a different question.

So the assertions here are about *where* and *how many*, not about values.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from vn_air_quality_weather.cities import CITIES, City
from vn_air_quality_weather.clients.open_aq import SensorSelection
from vn_air_quality_weather.models import ModeledAirQualityHourly
from vn_air_quality_weather.pipeline import _RawObjectCounts, _sample_model_at_stations
from vn_air_quality_weather.storage.raw_json import LocalRawJsonStore

MOMENT = datetime(2026, 8, 14, 6, 0, tzinfo=UTC)


class _FakeOpenMeteo:
    """Records the coordinates it was asked about, and answers with one hour."""

    def __init__(self) -> None:
        self.requested: list[tuple[float, float]] = []

    def fetch_modeled_air_quality(self, city: City, data_date: date) -> dict[str, Any]:
        del data_date
        self.requested.append((city.latitude, city.longitude))
        return {"probe": city.key}


def _fake_normalize(_city_key: str, _payload: Any) -> list[ModeledAirQualityHourly]:
    return [
        ModeledAirQualityHourly(
            city_key="hanoi",
            observed_at_utc=MOMENT,
            pm2_5=10.0,
            pm10=20.0,
            nitrogen_dioxide=3.0,
            ozone=40.0,
            grid_latitude=21.0,
            grid_longitude=105.8,
        )
    ]


def _run(selections: dict[str, SensorSelection], tmp_path: Path, monkeypatch) -> tuple:
    monkeypatch.setattr(
        "vn_air_quality_weather.pipeline.normalize_modeled_air_quality", _fake_normalize
    )
    open_meteo = _FakeOpenMeteo()
    records = _sample_model_at_stations(
        open_meteo=open_meteo,
        raw_store=LocalRawJsonStore(tmp_path),
        raw_counts=_RawObjectCounts(),
        city=CITIES["hanoi"],
        selections=selections,
        data_date=date(2026, 8, 14),
        ingestion_time=MOMENT,
        interval_start=MOMENT,
        interval_end=MOMENT,
        run_id="test-run",
    )
    return records, open_meteo


def test_the_model_is_sampled_at_the_station_not_the_anchor(tmp_path: Path, monkeypatch) -> None:
    """The anchor is what the gap is measured against. Sampling there again would
    make the representativeness term identically zero and prove nothing."""

    selections = {
        "pm25": SensorSelection("hanoi", "1001", "s", 1, "pm25", 21.0458, 105.8202),
    }

    records, open_meteo = _run(selections, tmp_path, monkeypatch)

    anchor = (CITIES["hanoi"].latitude, CITIES["hanoi"].longitude)
    assert open_meteo.requested == [(21.0458, 105.8202)]
    assert open_meteo.requested[0] != anchor
    assert records[0].station_latitude == 21.0458
    assert records[0].station_longitude == 105.8202
    assert records[0].station_id == "1001"


def test_one_call_per_station_not_per_sensor(tmp_path: Path, monkeypatch) -> None:
    """Four sensors at one station are one place. Asking four times would pay for
    the same answer four times against a rate-limited free tier."""

    selections = {
        pollutant: SensorSelection("hanoi", "1001", "s", index, pollutant, 21.0458, 105.8202)
        for index, pollutant in enumerate(("pm25", "pm10", "o3", "no2"))
    }

    _, open_meteo = _run(selections, tmp_path, monkeypatch)

    assert open_meteo.requested == [(21.0458, 105.8202)]


def test_two_stations_are_two_places(tmp_path: Path, monkeypatch) -> None:
    selections = {
        "pm25": SensorSelection("hanoi", "1001", "a", 1, "pm25", 21.0458, 105.8202),
        "o3": SensorSelection("hanoi", "1003", "b", 2, "o3", 21.0338, 105.8432),
    }

    _, open_meteo = _run(selections, tmp_path, monkeypatch)

    assert sorted(open_meteo.requested) == [(21.0338, 105.8432), (21.0458, 105.8202)]


def test_a_station_without_a_position_is_skipped_not_guessed(tmp_path: Path, monkeypatch) -> None:
    """Falling back to the city anchor would report a zero displacement for a station
    whose displacement is simply unknown. Those are different claims."""

    selections = {
        "pm25": SensorSelection("hanoi", "1001", "a", 1, "pm25", None, None),
    }

    records, open_meteo = _run(selections, tmp_path, monkeypatch)

    assert open_meteo.requested == []
    assert records == []
