"""End-to-end test of run_day with stubbed upstream APIs and a fake raw store."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from vn_air_quality_weather import pipeline as pipeline_module
from vn_air_quality_weather.cities import City
from vn_air_quality_weather.settings import Settings
from vn_air_quality_weather.storage.raw_json import RawWriteResult

DATA_DATE = date(2026, 7, 27)
HOURS = ["2026-07-27T00:00", "2026-07-27T01:00", "2026-07-27T02:00"]


class FakeOpenMeteoClient:
    def __init__(self, **_: Any) -> None:
        self.weather_calls: list[str] = []

    def fetch_weather(self, city: City, data_date: date) -> dict[str, Any]:
        self.weather_calls.append(city.key)
        return {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": {
                "time": HOURS,
                "temperature_2m": [29.0, 28.5, 28.0],
                "relative_humidity_2m": [70.0, 72.0, 74.0],
                "precipitation": [0.0, 1.2, 0.0],
                "wind_speed_10m": [8.0, 7.5, 7.0],
                "wind_direction_10m": [180.0, 190.0, 200.0],
            },
        }

    def fetch_modeled_air_quality(self, city: City, data_date: date) -> dict[str, Any]:
        return {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": {
                "time": HOURS,
                "pm2_5": [12.5, 13.0, None],
                "pm10": [20.0, 21.0, 22.0],
                "nitrogen_dioxide": [4.1, 4.2, 4.3],
                "ozone": [55.0, 56.0, 57.0],
            },
        }

    def __enter__(self) -> "FakeOpenMeteoClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeOpenAQClient:
    def __init__(self, api_key: str, **_: Any) -> None:
        assert api_key == "unit-test-key"

    def fetch_locations(self, city: City, radius_meters: int) -> dict[str, Any]:
        if city.key != "hanoi":
            return {"meta": {"found": 0}, "results": []}
        return {
            "meta": {"found": 1},
            "results": [
                {
                    "id": 9001,
                    "name": "Fake Hanoi station",
                    "datetimeLast": {"utc": "2026-07-27T23:00:00Z"},
                    "sensors": [{"id": 55, "parameter": {"name": "pm25"}}],
                }
            ],
        }

    def fetch_sensor_hours(
        self, sensor_id: int, interval_start: datetime, interval_end: datetime
    ) -> dict[str, Any]:
        return {
            "meta": {"returned": 2},
            "results": [
                {
                    "value": 15.1 + index,
                    "flagInfo": {"hasFlags": False},
                    "parameter": {"name": "pm25", "units": "µg/m³"},
                    "period": {"datetimeFrom": {"utc": f"2026-07-27T0{index}:00:00Z"}},
                }
                for index in range(2)
            ],
        }

    def __enter__(self) -> "FakeOpenAQClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class MemoryRawStore:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def write(self, **kwargs: Any) -> RawWriteResult:
        self.writes.append(kwargs)
        return RawWriteResult(
            location=f"memory://{len(self.writes)}",
            content_sha256="",
            created=True,
        )


@pytest.fixture
def stubbed_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "OpenMeteoClient", FakeOpenMeteoClient)
    monkeypatch.setattr(pipeline_module, "OpenAQClient", FakeOpenAQClient)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        openaq_api_key="unit-test-key",
        duckdb_path=tmp_path / "warehouse.duckdb",
        local_raw_root=tmp_path / "raw",
    )


def test_run_day_extracts_stores_and_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubbed_clients: None
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    store = MemoryRawStore()

    summary = pipeline_module.run_day(
        DATA_DATE,
        settings=_settings(tmp_path),
        raw_store=store,
        run_id="unit-test-run",
    )

    # 3 cities x (weather + modeled + openaq locations) = 9, plus one sensor-hours object.
    assert summary.raw_objects == 10
    assert summary.weather_rows == 9
    assert summary.observed_air_quality_rows == 2
    # pm2_5 is null in the third hour, so 3 cities x (3+3+3+2) modeled measurements.
    assert summary.modeled_air_quality_rows == 33
    assert {write["source"] for write in store.writes} == {
        "open_meteo_weather",
        "open_meteo_air_quality",
        "openaq_locations",
        "openaq_sensor_hours",
    }

    database_path = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(database_path), read_only=True) as connection:
        audit = connection.execute(
            """
            select run_id, weather_rows, observed_air_quality_rows, modeled_air_quality_rows
            from raw.pipeline_runs
            """
        ).fetchall()
        observed = connection.execute(
            "select count(*) from raw.air_quality_hourly where source_type = 'observed'"
        ).fetchone()

    assert audit == [("unit-test-run", 9, 2, 33)]
    assert observed == (2,)


def test_run_day_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubbed_clients: None
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    settings = _settings(tmp_path)

    for _ in range(2):
        pipeline_module.run_day(
            DATA_DATE,
            settings=settings,
            raw_store=MemoryRawStore(),
            run_id="unit-test-run",
        )

    with duckdb.connect(str(tmp_path / "warehouse.duckdb"), read_only=True) as connection:
        weather_rows = connection.execute("select count(*) from raw.weather_hourly").fetchone()
        air_rows = connection.execute("select count(*) from raw.air_quality_hourly").fetchone()
        run_rows = connection.execute("select count(*) from raw.pipeline_runs").fetchone()

    assert weather_rows == (9,)
    assert air_rows == (35,)
    assert run_rows == (1,)


def test_run_day_can_skip_openaq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubbed_clients: None
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))

    summary = pipeline_module.run_day(
        DATA_DATE,
        settings=_settings(tmp_path),
        raw_store=MemoryRawStore(),
        run_id="no-openaq",
        include_openaq=False,
    )

    assert summary.observed_air_quality_rows == 0
    assert summary.raw_objects == 6


def test_date_range_rejects_reversed_bounds() -> None:
    with pytest.raises(ValueError):
        pipeline_module._date_range(date(2026, 7, 28), date(2026, 7, 27))
    assert pipeline_module._date_range(DATA_DATE, DATA_DATE) == [DATA_DATE]


def test_create_raw_store_uses_local_backend(tmp_path: Path) -> None:
    store = pipeline_module.create_raw_store(_settings(tmp_path))
    result = store.write(
        source="open_meteo_weather",
        city_key="hanoi",
        ingestion_date=DATA_DATE,
        run_id="unit-test-run",
        payload={"metadata": {}, "response": {}},
    )
    assert result.created is True
    assert Path(result.location).exists()


def test_utc_interval_is_used_for_raw_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubbed_clients: None
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    store = MemoryRawStore()

    pipeline_module.run_day(
        DATA_DATE,
        settings=_settings(tmp_path),
        raw_store=store,
        run_id="unit-test-run",
        include_openaq=False,
    )

    metadata = store.writes[0]["payload"]["metadata"]
    assert metadata["interval_start"] == "2026-07-27T00:00:00Z"
    assert metadata["interval_end"] == "2026-07-28T00:00:00Z"
    assert metadata["airflow_run_id"] == "unit-test-run"


def test_observed_and_modeled_keep_source_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stubbed_clients: None
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    pipeline_module.run_day(
        DATA_DATE,
        settings=_settings(tmp_path),
        raw_store=MemoryRawStore(),
        run_id="unit-test-run",
    )

    with duckdb.connect(str(tmp_path / "warehouse.duckdb"), read_only=True) as connection:
        rows = connection.execute(
            """
            select source_type, source_name, count(*)
            from raw.air_quality_hourly
            group by 1, 2
            order by 1
            """
        ).fetchall()

    assert rows == [
        ("modeled", "open_meteo_cams", 33),
        ("observed", "openaq", 2),
    ]


def test_utc_datetime_normalization() -> None:
    from vn_air_quality_weather.clients.open_meteo import normalize_weather

    payload = {
        "latitude": 21.0,
        "longitude": 105.8,
        "hourly": {
            "time": ["2026-07-27T00:00Z"],
            "temperature_2m": [29.0],
            "relative_humidity_2m": [70.0],
            "precipitation": [0.0],
            "wind_speed_10m": [8.0],
            "wind_direction_10m": [180.0],
        },
    }
    records = normalize_weather("hanoi", payload)
    assert records[0].observed_at_utc == datetime(2026, 7, 27, tzinfo=UTC)
