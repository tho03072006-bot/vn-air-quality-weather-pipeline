"""run_forecast outcome accounting: SUCCESS, PARTIAL, FAILED and raw-object counts.

The point of these tests is that a national run has 34 independent anchors, so
"did it work" is not a boolean. Before the partial-success work, one failing
anchor discarded every anchor that had already landed and left no record of what
went wrong.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from vn_air_quality_weather import forecast_pipeline as forecast_module
from vn_air_quality_weather.clients.open_meteo import (
    AIR_QUALITY_FORECAST_VARIABLES,
    WEATHER_FORECAST_VARIABLES,
)
from vn_air_quality_weather.forecast_pipeline import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    ForecastLocationError,
    redact,
    run_forecast,
    selected_provinces,
)
from vn_air_quality_weather.settings import Settings
from vn_air_quality_weather.storage.raw_json import RawWriteResult

ISSUED_AT = datetime(2026, 8, 8, 6, 0, tzinfo=UTC)
FORECAST_HOURS = 3
HOURS = ["2026-08-08T06:00", "2026-08-08T07:00", "2026-08-08T08:00"]


def _hourly(variables: tuple[str, ...]) -> dict[str, Any]:
    return {"time": HOURS, **{name: [1.0, 2.0, 3.0] for name in variables}}


class FakeForecastClient:
    """Returns valid payloads, but raises for anchors named in failing_keys."""

    def __init__(self, failing_keys: frozenset[str] = frozenset(), **_: Any) -> None:
        self.failing_keys = failing_keys
        self.calls: list[str] = []

    def fetch_air_quality_forecast(self, location: Any, hours: int) -> dict[str, Any]:
        self.calls.append(location.key)
        if location.key in self.failing_keys:
            raise httpx.ReadTimeout(
                "timed out for https://api.open-meteo.com/v1/air-quality?apikey=super-secret"
            )
        return {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "hourly": _hourly(AIR_QUALITY_FORECAST_VARIABLES),
        }

    def fetch_weather_forecast(self, location: Any, hours: int) -> dict[str, Any]:
        return {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "hourly": _hourly(WEATHER_FORECAST_VARIABLES),
        }

    def __enter__(self) -> "FakeForecastClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class MemoryRawStore:
    """Reports the second write of an identical key as reused, like the real store."""

    def __init__(self) -> None:
        self.keys: list[tuple[str, str]] = []

    def write(self, **kwargs: Any) -> RawWriteResult:
        key = (str(kwargs["source"]), str(kwargs["city_key"]))
        created = key not in self.keys
        self.keys.append(key)
        return RawWriteResult(location=f"memory://{key}", content_sha256="", created=created)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        duckdb_path=tmp_path / "warehouse.duckdb",
        local_raw_root=tmp_path / "raw",
    )


def _install_client(
    monkeypatch: pytest.MonkeyPatch, failing_keys: frozenset[str] = frozenset()
) -> None:
    monkeypatch.setattr(
        forecast_module,
        "OpenMeteoClient",
        lambda **kwargs: FakeForecastClient(failing_keys=failing_keys),
    )


def _run(tmp_path: Path, store: MemoryRawStore, keys: list[str], **kwargs: Any):
    return run_forecast(
        provinces=selected_provinces(keys, False),
        forecast_hours=FORECAST_HOURS,
        settings=_settings(tmp_path),
        raw_store=store,
        run_id="unit-test-forecast",
        forecast_issued_at_utc=ISSUED_AT,
        **kwargs,
    )


def test_every_anchor_landing_is_audited_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    _install_client(monkeypatch)
    store = MemoryRawStore()

    summary = _run(tmp_path, store, ["hanoi", "hue"], max_workers=1)

    assert summary.status == STATUS_SUCCESS
    assert summary.succeeded_location_keys == ("hanoi", "hue")
    assert summary.failed_location_keys == ()
    # Two anchors x (air + weather) raw objects, all new.
    assert (summary.raw_objects, summary.raw_objects_created, summary.raw_objects_reused) == (
        4,
        4,
        0,
    )
    assert summary.air_quality_forecast_rows == 2 * len(HOURS) * len(AIR_QUALITY_FORECAST_VARIABLES)
    assert summary.weather_forecast_rows == 2 * len(HOURS)


def test_one_failing_anchor_still_loads_the_others_as_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    _install_client(monkeypatch, frozenset({"hue"}))
    store = MemoryRawStore()

    summary = _run(tmp_path, store, ["hanoi", "hue", "da_nang"], max_workers=1)

    assert summary.status == STATUS_PARTIAL
    assert summary.succeeded_location_keys == ("hanoi", "da_nang")
    assert summary.failed_location_keys == ("hue",)
    # The two healthy anchors still landed rather than being discarded.
    assert summary.weather_forecast_rows == 2 * len(HOURS)
    # The failing anchor wrote nothing, so it contributes no raw objects.
    assert summary.raw_objects == 4


def test_a_run_where_every_anchor_fails_raises_after_being_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    _install_client(monkeypatch, frozenset({"hanoi", "hue"}))
    store = MemoryRawStore()

    # Raised so Airflow retries a dead run, but only after the audit row exists.
    with pytest.raises(ForecastLocationError):
        _run(tmp_path, store, ["hanoi", "hue"], max_workers=1)

    import duckdb

    with duckdb.connect(str(tmp_path / "warehouse.duckdb"), read_only=True) as connection:
        row = connection.execute(
            "select status, succeeded_location_count, failed_location_count, error_category "
            "from raw.pipeline_runs"
        ).fetchone()
    assert row == (STATUS_FAILED, 0, 2, "ReadTimeout")


def test_reused_raw_objects_are_not_counted_as_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    _install_client(monkeypatch)
    store = MemoryRawStore()

    first = _run(tmp_path, store, ["hanoi"], max_workers=1)
    # Same store, same keys: the second run finds both objects already present.
    second = _run(tmp_path, store, ["hanoi"], max_workers=1)

    assert (first.raw_objects_created, first.raw_objects_reused) == (2, 0)
    assert (second.raw_objects_created, second.raw_objects_reused) == (0, 2)
    assert second.raw_objects == 2


def test_concurrent_workers_preserve_deterministic_anchor_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    _install_client(monkeypatch)
    store = MemoryRawStore()

    summary = _run(tmp_path, store, ["hanoi", "hue", "da_nang"], max_workers=3)

    # pool.map yields in submission order, so the audit reads the same whichever
    # anchor happens to return first.
    assert summary.succeeded_location_keys == ("hanoi", "hue", "da_nang")
    assert summary.status == STATUS_SUCCESS


def test_persisted_error_summary_drops_query_strings() -> None:
    # An error summary is stored and then rendered in the dashboard, so a URL
    # carrying a credential must not survive into it.
    assert redact("failed https://api.example/v1?apikey=super-secret now") == (
        "failed https://api.example/v1?<redacted> now"
    )


def test_failed_anchor_names_are_recorded_so_a_resume_is_possible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt-state"))
    _install_client(monkeypatch, frozenset({"hue"}))
    store = MemoryRawStore()

    _run(tmp_path, store, ["hanoi", "hue"], max_workers=1)

    import duckdb

    with duckdb.connect(str(tmp_path / "warehouse.duckdb"), read_only=True) as connection:
        summary_text = connection.execute("select error_summary from raw.pipeline_runs").fetchone()
    assert summary_text is not None
    assert "hue" in summary_text[0]
    assert "super-secret" not in summary_text[0]


def test_forecast_hours_and_scope_are_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one province"):
        run_forecast(provinces=(), settings=_settings(tmp_path))
    with pytest.raises(ValueError, match="between 1 and 168"):
        run_forecast(
            provinces=selected_provinces(["hanoi"], False),
            forecast_hours=0,
            settings=_settings(tmp_path),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        run_forecast(
            provinces=selected_provinces(["hanoi"], False),
            settings=_settings(tmp_path),
            forecast_issued_at_utc=datetime(2026, 8, 8, 6, 0),
        )
