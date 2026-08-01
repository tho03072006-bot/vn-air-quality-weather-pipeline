"""Unit tests for the pipeline command-line orchestration."""

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vn_air_quality_weather import pipeline
from vn_air_quality_weather.settings import Settings
from vn_air_quality_weather.storage.raw_json import LocalRawJsonStore


def _settings(tmp_path: Path, *, raw_backend: str = "local") -> Settings:
    return Settings(
        _env_file=None,
        project_env="test",
        openaq_api_key="unit-test-key",
        duckdb_path=tmp_path / "warehouse.duckdb",
        local_raw_root=tmp_path / "raw",
        raw_backend=raw_backend,
    )


def _summary(data_date: date) -> SimpleNamespace:
    return SimpleNamespace(
        data_date=data_date,
        raw_objects=3,
        weather_rows=24,
        observed_air_quality_rows=2,
        modeled_air_quality_rows=96,
    )


def test_date_range_is_inclusive() -> None:
    start = date(2026, 7, 25)
    end = date(2026, 7, 27)

    assert pipeline._date_range(start, end) == [
        date(2026, 7, 25),
        date(2026, 7, 26),
        date(2026, 7, 27),
    ]
    assert pipeline._date_range(start, start) == [start]


def test_date_range_rejects_reversed_bounds() -> None:
    with pytest.raises(ValueError, match="start date must be on or before end date"):
        pipeline._date_range(date(2026, 7, 28), date(2026, 7, 27))


@pytest.mark.parametrize(
    "arguments",
    [
        ["--date", "2026-07-27", "--start-date", "2026-07-26", "--end-date", "2026-07-27"],
        [],
        ["--start-date", "2026-07-26"],
        ["--end-date", "2026-07-27"],
    ],
)
def test_main_rejects_conflicting_or_incomplete_dates(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["vn-air-pipeline", *arguments])

    with pytest.raises(SystemExit):
        pipeline.main()


def test_main_runs_one_date_and_builds_dbt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    run_calls: list[tuple[date, bool]] = []
    dbt_calls: list[Path] = []

    def fake_run_day(data_date: date, **kwargs: Any) -> SimpleNamespace:
        run_calls.append((data_date, kwargs["include_openaq"]))
        assert kwargs["settings"] is settings
        return _summary(data_date)

    monkeypatch.setattr(sys, "argv", ["vn-air-pipeline", "--date", "2026-07-27"])
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "run_day", fake_run_day)
    monkeypatch.setattr(pipeline, "run_dbt_build", dbt_calls.append)

    pipeline.main()

    assert run_calls == [(date(2026, 7, 27), True)]
    assert dbt_calls == [settings.duckdb_path]


def test_main_runs_inclusive_range_and_can_skip_dbt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    run_calls: list[tuple[date, bool]] = []
    dbt_calls: list[Path] = []

    def fake_run_day(data_date: date, **kwargs: Any) -> SimpleNamespace:
        run_calls.append((data_date, kwargs["include_openaq"]))
        return _summary(data_date)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vn-air-pipeline",
            "--start-date",
            "2026-07-25",
            "--end-date",
            "2026-07-27",
            "--skip-openaq",
            "--skip-dbt",
        ],
    )
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "run_day", fake_run_day)
    monkeypatch.setattr(pipeline, "run_dbt_build", dbt_calls.append)

    pipeline.main()

    assert run_calls == [
        (date(2026, 7, 25), False),
        (date(2026, 7, 26), False),
        (date(2026, 7, 27), False),
    ]
    assert dbt_calls == []


def test_create_raw_store_uses_local_backend(tmp_path: Path) -> None:
    store = pipeline.create_raw_store(_settings(tmp_path))

    assert isinstance(store, LocalRawJsonStore)


def test_create_raw_store_requires_complete_s3_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path, raw_backend="s3")

    with pytest.raises(ValueError, match="AWS_REGION and AWS_S3_BUCKET"):
        pipeline.create_raw_store(settings)
