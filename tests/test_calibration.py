"""Discriminating experiments and grain checks for station-local shadow calibration."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from vn_air_quality_weather.calibration import (
    DataValidationError,
    PairingResult,
    StationPair,
    StationReading,
    evaluate_station_calibration,
    load_station_pairs,
    pair_station_readings,
)

START = datetime(2026, 8, 1, tzinfo=UTC)


def reading(hour: int = 0, value: float | None = 10) -> StationReading:
    return StationReading(
        "station-1",
        "hanoi",
        "pm25",
        START + timedelta(hours=hour),
        value,
        "µg/m³",
        21.0458,
        105.8202,
    )


def series(offsets: list[float], station: str = "station-1") -> list[StationPair]:
    return [
        StationPair(
            station,
            "hanoi",
            "pm25",
            START + timedelta(hours=hour),
            20 + hour % 7 + offset,
            20 + hour % 7,
            21.0458,
            105.8202,
        )
        for hour, offset in enumerate(offsets)
    ]


def evaluate(pairs: list[StationPair], **kwargs) -> dict:
    return evaluate_station_calibration(
        PairingResult(tuple(pairs)), min_train_hours=6, min_holdout_hours=2, **kwargs
    )


def test_a_consistent_station_offset_improves_on_later_unseen_hours() -> None:
    report = evaluate(series([10] * 8))
    result = report["series"][0]
    assert report["target"] == "colocated_station_analysis"
    assert report["serving_changed"] is False
    assert result["offset_ugm3"] == 10
    assert result["baseline_holdout"]["mae_ugm3"] == 10
    assert result["candidate_holdout"]["mae_ugm3"] == 0
    assert result["improves_holdout"] is True


def test_a_reversed_holdout_rejects_the_previously_good_offset() -> None:
    result = evaluate(series([10] * 6 + [-10] * 2))["series"][0]
    assert result["offset_ugm3"] == 10
    assert result["candidate_holdout"]["mae_ugm3"] == 20
    assert result["improves_holdout"] is False


def test_future_observations_cannot_change_the_fitted_offset() -> None:
    # A constant training residual would let a leaking median remain unchanged:
    # training would still own the majority of the sample. Varied residuals make
    # contamination observable, and moving the two future points flips its direction.
    past = series([0, 2, 4, 6, 8, 10, 100, 100])
    changed_future = [replace(p, observed_ugm3=1000) if i >= 6 else p for i, p in enumerate(past)]
    before, after = evaluate(past), evaluate(changed_future)
    assert before["series"][0]["offset_ugm3"] == after["series"][0]["offset_ugm3"] == 5
    assert before["series"][0]["candidate_holdout"] != after["series"][0]["candidate_holdout"]


def test_robust_fitting_uses_a_median_and_never_the_holdout_average() -> None:
    result = evaluate(series([10] * 5 + [1000] + [10] * 2))["series"][0]
    assert result["offset_ugm3"] == 10
    assert result["candidate_holdout"]["rmse_ugm3"] == 0


def test_same_valid_hour_in_all_series_is_on_one_side_of_the_cut() -> None:
    report = evaluate(series([10] * 8) + series([20] * 8, station="station-2"))
    assert report["distinct_valid_hours"] == 8
    assert report["paired_hours_across_series"] == 16
    for result in report["series"]:
        assert result["train_hours"] == 6
        assert result["holdout_hours"] == 2
        assert result["last_training_hour_utc"] == (START + timedelta(hours=5)).isoformat()
        assert result["first_holdout_hour_utc"] == report["holdout_starts_at_utc"]


def test_corrected_concentrations_are_clamped_at_zero() -> None:
    pairs = series([20] * 8)
    pairs[6:] = [replace(p, model_ugm3=5, observed_ugm3=0) for p in pairs[6:]]
    result = evaluate(pairs)["series"][0]
    assert result["candidate_holdout"]["mae_ugm3"] == 0
    assert result["candidate_holdout"]["mean_signed_gap_ugm3"] == 0


def test_sparse_history_is_not_a_zero_metric_or_an_accuracy_claim() -> None:
    report = evaluate(series([10] * 4))
    assert report["status"] == "insufficient_data"
    assert report["series"][0]["baseline_holdout"] is None
    assert report["series"][0]["candidate_holdout"] is None
    assert report["series"][0]["improves_holdout"] is None
    assert evaluate([])["series"] == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"train_fraction": 0},
        {"train_fraction": 1},
        {"min_train_hours": 0},
        {"min_holdout_hours": 0},
    ],
)
def test_invalid_experimental_policy_is_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError, match="train_fraction"):
        evaluate_station_calibration(PairingResult(), **kwargs)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"flagged": True}, "flagged_observation"),
        ({"flagged": None}, "unknown_quality_flag"),
        ({"unit": "ppm"}, "unit_mismatch_or_unsupported"),
        ({"unit": None}, "unit_mismatch_or_unsupported"),
        ({"concentration": None}, "missing_or_invalid_concentration"),
        ({"concentration": float("nan")}, "missing_or_invalid_concentration"),
        ({"concentration": -1}, "missing_or_invalid_concentration"),
        ({"latitude": None}, "missing_position"),
    ],
)
def test_invalid_measurements_are_excluded_and_counted(changes: dict, reason: str) -> None:
    result = pair_station_readings([reading(value=20)], [replace(reading(), **changes)])
    assert result.pairs == ()
    assert result.exclusions == {reason: 1}


def test_unit_spellings_are_normalized_without_converting_different_units() -> None:
    result = pair_station_readings([reading(value=20)], [replace(reading(), unit="ug/m3")])
    assert len(result.pairs) == 1
    assert result.pairs[0].observed_ugm3 == 10


def test_missing_model_value_is_also_excluded() -> None:
    result = pair_station_readings([reading(value=None)], [reading()])
    assert result.exclusions == {"missing_or_invalid_concentration": 1}


def test_timezone_offsets_join_the_same_utc_hour_without_rounding() -> None:
    offset = START.astimezone(timezone(timedelta(hours=7)))
    result = pair_station_readings([reading()], [replace(reading(), valid_at_utc=offset)])
    assert result.pairs[0].valid_at_utc == START


@pytest.mark.parametrize("label", ["model", "observation"])
def test_duplicate_grain_never_inflates_sample_size(label: str) -> None:
    models, observations = [reading(value=20)], [reading()]
    (models if label == "model" else observations).append(reading())
    with pytest.raises(DataValidationError, match=f"duplicate {label}"):
        pair_station_readings(models, observations)


def test_model_and_instrument_must_carry_the_same_requested_station_position() -> None:
    with pytest.raises(DataValidationError, match="coordinates do not match"):
        pair_station_readings([reading()], [replace(reading(), latitude=21.5)])


def test_a_moved_station_requires_a_separate_experiment() -> None:
    with pytest.raises(DataValidationError, match="position changed"):
        pair_station_readings([reading(), replace(reading(1), latitude=21.5)], [])


@pytest.mark.parametrize(
    "changes",
    [
        {"latitude": 91},
        {"longitude": 181},
        {"latitude": float("inf")},
        {"valid_at_utc": START.replace(tzinfo=None)},
        {"valid_at_utc": START + timedelta(minutes=1)},
        {"station_id": ""},
    ],
)
def test_invalid_coordinates_keys_and_hour_metadata_fail_explicitly(changes: dict) -> None:
    with pytest.raises(DataValidationError):
        pair_station_readings([replace(reading(), **changes)], [])


def test_station_and_city_identity_are_never_averaged() -> None:
    result = pair_station_readings([reading()], [replace(reading(), station_id="station-2")])
    assert result.pairs == ()
    assert result.exclusions == {"unmatched_model": 1, "unmatched_observation": 1}
    with pytest.raises(DataValidationError, match="city key"):
        pair_station_readings([reading()], [replace(reading(), city_key="hcm")])
    with pytest.raises(DataValidationError, match="multiple city keys"):
        pair_station_readings([reading(), replace(reading(1), city_key="hcm")], [])


def test_public_evaluator_cannot_bypass_pairing_validation() -> None:
    pairs = series([10] * 8)
    with pytest.raises(DataValidationError, match="duplicate paired"):
        evaluate([*pairs, pairs[0]])
    with pytest.raises(DataValidationError, match="nonnegative"):
        evaluate([replace(pairs[0], observed_ugm3=-1)])


@pytest.mark.parametrize(
    "changes",
    [
        {"latitude": None},
        {"longitude": None},
        {"latitude": None, "longitude": None},
    ],
)
def test_public_evaluator_requires_both_coordinates_even_with_enough_hours(changes: dict) -> None:
    pairs = [replace(pair, **changes) for pair in series([10] * 8)]
    with pytest.raises(DataValidationError, match="require both station coordinates"):
        evaluate(pairs)


def create_warehouse(path: Path) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE SCHEMA raw")
        connection.execute(
            "CREATE TABLE raw.air_quality_at_station_hourly (station_id VARCHAR, "
            "city_key VARCHAR, pollutant VARCHAR, observed_at_utc TIMESTAMPTZ, "
            "value DOUBLE, unit VARCHAR, station_latitude DOUBLE, station_longitude DOUBLE, "
            "source_name VARCHAR, source_type VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE raw.air_quality_hourly AS "
            "SELECT *, FALSE AS flagged FROM raw.air_quality_at_station_hourly"
        )
        connection.execute(
            "INSERT INTO raw.air_quality_at_station_hourly VALUES "
            "('s1','hanoi','pm25',?,20,'µg/m³',21.0,105.8,'open_meteo_cams','modeled_at_station')",
            [START],
        )
        connection.execute(
            "INSERT INTO raw.air_quality_hourly VALUES "
            "('s1','hanoi','pm25',?,10,'µg/m³',21.0,105.8,'openaq','observed',FALSE), "
            "('other','hanoi','pm25',?,1000,'µg/m³',21.1,105.9,'openaq','observed',FALSE)",
            [START, START],
        )


def test_loader_reads_station_grain_and_leaves_warehouse_bytes_untouched(tmp_path: Path) -> None:
    database = tmp_path / "sample.duckdb"
    create_warehouse(database)
    before = hashlib.sha256(database.read_bytes()).digest()
    result = load_station_pairs(database)
    assert len(result.pairs) == 1
    assert result.pairs[0].observed_ugm3 == 10  # Never the two-station city average.
    assert result.source_counts == {"model": 1, "observation": 2}
    assert result.exclusions == {"unmatched_observation": 1}
    assert hashlib.sha256(database.read_bytes()).digest() == before


def test_missing_old_schema_is_readiness_not_fake_zero(tmp_path: Path) -> None:
    database = tmp_path / "old.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE unrelated (value INTEGER)")
    report = evaluate_station_calibration(load_station_pairs(database))
    assert report["status"] == "not_ready"
    assert report["blockers"] == [
        "missing raw.air_quality_at_station_hourly",
        "missing raw.air_quality_hourly",
    ]
    assert report["series"] == []
    with pytest.raises(FileNotFoundError):
        load_station_pairs(tmp_path / "nonexistent.duckdb")
    assert not (tmp_path / "nonexistent.duckdb").exists()


def test_an_old_positionless_measurement_remains_unusable(tmp_path: Path) -> None:
    database = tmp_path / "no-position.duckdb"
    create_warehouse(database)
    with duckdb.connect(str(database)) as connection:
        connection.execute("ALTER TABLE raw.air_quality_hourly DROP COLUMN station_latitude")
        connection.execute("ALTER TABLE raw.air_quality_hourly DROP COLUMN station_longitude")
    result = load_station_pairs(database)
    assert result.pairs == ()
    assert result.exclusions["missing_position"] == 1
    assert evaluate_station_calibration(result)["status"] == "insufficient_data"


def test_missing_quality_flags_block_the_experiment(tmp_path: Path) -> None:
    database = tmp_path / "no-flag.duckdb"
    create_warehouse(database)
    with duckdb.connect(str(database)) as connection:
        connection.execute("ALTER TABLE raw.air_quality_hourly DROP COLUMN flagged")
    report = evaluate_station_calibration(load_station_pairs(database))
    assert report["status"] == "not_ready"
    assert report["blockers"] == ["raw.air_quality_hourly missing columns: flagged"]


def test_loader_does_not_read_city_anchor_model_rows_as_observations(tmp_path: Path) -> None:
    database = tmp_path / "sources.duckdb"
    create_warehouse(database)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "INSERT INTO raw.air_quality_hourly VALUES "
            "('s1','hanoi','pm25',?,1000,'µg/m³',21.0,105.8,'open_meteo_cams','modeled',FALSE)",
            [START],
        )
    result = load_station_pairs(database)
    assert result.source_counts["observation"] == 2
    assert result.pairs[0].observed_ugm3 == 10


def test_cli_json_requires_an_explicit_database_and_never_creates_one(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_station_calibration.py"
    missing = tmp_path / "missing.duckdb"
    completed = subprocess.run(
        [sys.executable, str(script), "--database", str(missing)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["status"] == "invalid_or_unreadable_data"
    assert not missing.exists()
