"""Offline, station-local shadow evaluation of a historical CAMS analysis offset.

This does not train CAMS or measure forecast accuracy. The warehouse currently has
historical analysis sampled at station coordinates, not station-coordinate forecast
vintages. Province forecasts and city-averaged observations are never used here.
Nothing from this module changes serving data, outdoor scores or public claims.

The candidate subtracts the training sample's median(model - observation), then
clamps concentrations at zero. A single chronological cut groups every station's
same valid hour on the same side; the held-out observations never enter fitting.
The defaults (72 training hours, 24 held-out hours, 75% training) are configurable
evaluation guards, not product thresholds or a claim of statistical reliability.
One holdout can establish a local experimental result, not transferability to
other stations, seasons, pollutants or future forecast vintages.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

import duckdb

TARGET = "colocated_station_analysis"
_COORDINATE_PRECISION = 1e-6  # Metadata rounding tolerance, never a geographic radius.
_MICROGRAM_UNITS = {
    "µg/m³",
    "ug/m3",
    "ugm3",
    "µg/m3",
    "ug/m³",
    "micrograms per cubic meter",
}


class DataValidationError(ValueError):
    """An ambiguous grain or station position makes the experiment invalid."""


@dataclass(frozen=True, slots=True)
class StationReading:
    station_id: str
    city_key: str
    pollutant: str
    valid_at_utc: datetime
    concentration: float | None
    unit: str | None
    latitude: float | None
    longitude: float | None
    flagged: bool | None = False


@dataclass(frozen=True, slots=True)
class StationPair:
    station_id: str
    city_key: str
    pollutant: str
    valid_at_utc: datetime
    model_ugm3: float
    observed_ugm3: float
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class PairingResult:
    pairs: tuple[StationPair, ...] = ()
    source_counts: dict[str, int] = field(default_factory=dict)
    exclusions: dict[str, int] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()


def _utc_hour(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataValidationError("valid_at_utc must be a timezone-aware datetime")
    result = value.astimezone(UTC)
    if result.minute or result.second or result.microsecond:
        raise DataValidationError("valid_at_utc must be an exact UTC hour; no rounding is applied")
    return result


def _position(reading: StationReading) -> tuple[float, float] | None:
    lat, lon = reading.latitude, reading.longitude
    if lat is None or lon is None:
        return None
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90):
        raise DataValidationError("invalid station coordinate metadata")
    if not -180 <= lon <= 180:
        raise DataValidationError("invalid station coordinate metadata")
    return lat, lon


def _same_position(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return all(
        math.isclose(a, b, rel_tol=0, abs_tol=_COORDINATE_PRECISION)
        for a, b in zip(left, right, strict=True)
    )


def _indexed(readings: list[StationReading], label: str) -> dict[tuple, StationReading]:
    indexed: dict[tuple, StationReading] = {}
    positions: dict[str, tuple[float, float]] = {}
    cities: dict[str, str] = {}
    for reading in readings:
        if not reading.station_id or not reading.city_key or not reading.pollutant:
            raise DataValidationError("station_id, city_key and pollutant are required")
        key = (reading.station_id, reading.pollutant.lower(), _utc_hour(reading.valid_at_utc))
        if key in indexed:
            # A second source/sensor/vintage at this grain must be resolved upstream,
            # not averaged here and not allowed to increase the apparent sample size.
            raise DataValidationError(f"duplicate {label} station/pollutant/UTC-hour")
        indexed[key] = reading
        if reading.station_id in cities and cities[reading.station_id] != reading.city_key:
            raise DataValidationError("one station_id carries multiple city keys")
        cities[reading.station_id] = reading.city_key
        position = _position(reading)
        if position is not None:
            previous = positions.get(reading.station_id)
            if previous is not None and not _same_position(previous, position):
                raise DataValidationError("station position changed; split its history explicitly")
            positions[reading.station_id] = position
    return indexed


def pair_station_readings(
    modeled: list[StationReading], observed: list[StationReading]
) -> PairingResult:
    """Join exact station/pollutant/hour keys; report every excluded pair candidate."""

    models = _indexed(modeled, "model")
    observations = _indexed(observed, "observation")
    exclusions: Counter[str] = Counter()
    pairs: list[StationPair] = []
    for key in sorted(models.keys() | observations.keys()):
        model, observation = models.get(key), observations.get(key)
        if model is None or observation is None:
            exclusions["unmatched_model" if observation is None else "unmatched_observation"] += 1
            continue
        if observation.flagged is not False:
            exclusions[
                "flagged_observation" if observation.flagged else "unknown_quality_flag"
            ] += 1
            continue
        if model.city_key != observation.city_key:
            raise DataValidationError("model and observation disagree on station city key")
        units = [str(row.unit).strip().lower() for row in (model, observation)]
        if any(unit not in _MICROGRAM_UNITS for unit in units):
            exclusions["unit_mismatch_or_unsupported"] += 1
            continue
        values = (model.concentration, observation.concentration)
        if any(value is None or not math.isfinite(value) or value < 0 for value in values):
            exclusions["missing_or_invalid_concentration"] += 1
            continue
        model_position, observed_position = _position(model), _position(observation)
        if model_position is None or observed_position is None:
            exclusions["missing_position"] += 1
            continue
        if not _same_position(model_position, observed_position):
            raise DataValidationError("model and observation station coordinates do not match")
        pairs.append(
            StationPair(
                model.station_id,
                model.city_key,
                model.pollutant.lower(),
                key[2],
                float(model.concentration),
                float(observation.concentration),
                *model_position,
            )
        )
    return PairingResult(
        tuple(pairs), {"model": len(modeled), "observation": len(observed)}, dict(exclusions)
    )


def load_station_pairs(database: Path) -> PairingResult:
    """Read only raw station analysis and observations, without Settings or secrets.

    Requested station coordinates are checked against the instrument's metadata;
    the grid cell centre is intentionally not asserted equal to a street location.
    Missing position columns in older history remain missing, never backfilled.
    """

    if not database.is_file():
        raise FileNotFoundError("explicit warehouse file does not exist")
    specifications = (
        ("air_quality_at_station_hourly", "modeled_at_station", "open_meteo_cams"),
        ("air_quality_hourly", "observed", "openaq"),
    )
    sources: list[list[StationReading]] = []
    blockers: list[str] = []
    required = {
        "station_id",
        "city_key",
        "pollutant",
        "observed_at_utc",
        "value",
        "unit",
        "source_type",
        "source_name",
    }
    with duckdb.connect(str(database.resolve()), read_only=True) as connection:
        for table, source_type, source_name in specifications:
            columns = {
                row[0]
                for row in connection.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema = 'raw' and table_name = ?",
                    [table],
                ).fetchall()
            }
            if not columns:
                blockers.append(f"missing raw.{table}")
                sources.append([])
                continue
            missing = required - columns
            if source_type == "observed" and "flagged" not in columns:
                missing.add("flagged")
            if missing:
                blockers.append(f"raw.{table} missing columns: {', '.join(sorted(missing))}")
                sources.append([])
                continue
            latitude = "station_latitude" if "station_latitude" in columns else "NULL"
            longitude = "station_longitude" if "station_longitude" in columns else "NULL"
            flagged = "flagged" if source_type == "observed" else "FALSE"
            # Identifiers come only from the fixed specifications above. Values are
            # bound, and no SQL from command-line input is interpolated.
            rows = connection.execute(
                f"SELECT station_id, city_key, pollutant, observed_at_utc, value, unit, "
                f'{latitude}, {longitude}, {flagged} FROM raw."{table}" '
                "WHERE source_type = ? AND source_name = ?",
                [source_type, source_name],
            ).fetchall()
            sources.append([StationReading(*row) for row in rows])
    result = pair_station_readings(*sources)
    return PairingResult(result.pairs, result.source_counts, result.exclusions, tuple(blockers))


def _metrics(pairs: list[StationPair], offset: float = 0) -> dict[str, float]:
    differences = [max(0, pair.model_ugm3 - offset) - pair.observed_ugm3 for pair in pairs]
    return {
        "mae_ugm3": mean(abs(value) for value in differences),
        "rmse_ugm3": math.sqrt(mean(value * value for value in differences)),
        "mean_signed_gap_ugm3": mean(differences),
    }


def evaluate_station_calibration(
    result: PairingResult,
    *,
    train_fraction: float = 0.75,
    min_train_hours: int = 72,
    min_holdout_hours: int = 24,
) -> dict[str, Any]:
    """Evaluate a fixed candidate per station/pollutant on unseen later valid hours."""

    if not 0 < train_fraction < 1 or min_train_hours < 1 or min_holdout_hours < 1:
        raise ValueError("use 0 < train_fraction < 1 and positive minimum hour counts")
    pairs = list(result.pairs)
    # Public callers cannot bypass pairing's grain and metadata guards.
    # Missing positions are exclusions while constructing pairs, but an already
    # paired public input must not present a positionless comparison as colocated.
    if any(pair.latitude is None or pair.longitude is None for pair in pairs):
        raise DataValidationError("paired inputs require both station coordinates")
    _indexed(
        [
            StationReading(
                p.station_id,
                p.city_key,
                p.pollutant,
                p.valid_at_utc,
                p.model_ugm3,
                "µg/m³",
                p.latitude,
                p.longitude,
            )
            for p in pairs
        ],
        "paired",
    )
    if any(
        not math.isfinite(value) or value < 0
        for pair in pairs
        for value in (pair.model_ugm3, pair.observed_ugm3)
    ):
        raise DataValidationError("paired concentrations must be finite and nonnegative")
    hours = sorted({_utc_hour(pair.valid_at_utc) for pair in pairs})
    cut_index = math.floor(len(hours) * train_fraction)
    cut = hours[cut_index] if cut_index < len(hours) else None
    report: dict[str, Any] = {
        "target": TARGET,
        "mode": "station_local_shadow_only",
        "status": "insufficient_data",
        "scope": "Historical station-coordinate CAMS analysis, not forecast accuracy or retraining",
        "serving_changed": False,
        "policy": {
            "train_fraction": train_fraction,
            "min_train_hours": min_train_hours,
            "min_holdout_hours": min_holdout_hours,
            "split": "global chronological distinct UTC valid hour; no random split",
            "candidate": "subtract training-only median signed gap; clamp at zero",
            "improvement": "held-out MAE and RMSE must both strictly decrease",
            "defaults_are": "configurable experimental guards, not product thresholds",
        },
        "source_counts": result.source_counts,
        "paired_hours_across_series": len(pairs),
        "distinct_valid_hours": len(hours),
        "exclusions": result.exclusions,
        "blockers": list(result.blockers),
        "holdout_starts_at_utc": cut.isoformat() if cut else None,
        "series": [],
        "limitations": [
            "Station-local offset cannot be transferred to a province anchor "
            "or an unobserved station",
            "Requested station coordinates are sampled from a model grid, "
            "not an instrument at the station",
            "Historical analysis may use information unavailable at forecast issuance",
            "One chronological holdout does not establish seasonal or future stability",
            "Instrument error and sub-grid representativeness remain in the comparison",
        ],
    }
    if result.blockers:
        report["status"] = "not_ready"
        return report
    groups: dict[tuple[str, str], list[StationPair]] = defaultdict(list)
    for pair in pairs:
        groups[(pair.station_id, pair.pollutant.lower())].append(pair)
    for (station_id, pollutant), group in sorted(groups.items()):
        training = [p for p in group if cut and _utc_hour(p.valid_at_utc) < cut]
        holdout = [p for p in group if cut and _utc_hour(p.valid_at_utc) >= cut]
        item: dict[str, Any] = {
            "station_id": station_id,
            "city_key": group[0].city_key,
            "pollutant": pollutant,
            "status": "insufficient_data",
            "train_hours": len(training),
            "holdout_hours": len(holdout),
            "offset_ugm3": None,
            "last_training_hour_utc": max(
                (_utc_hour(p.valid_at_utc) for p in training), default=None
            ).isoformat()
            if training
            else None,
            "first_holdout_hour_utc": min(
                (_utc_hour(p.valid_at_utc) for p in holdout), default=None
            ).isoformat()
            if holdout
            else None,
            "baseline_holdout": None,
            "candidate_holdout": None,
            "improves_holdout": None,
        }
        if len(training) >= min_train_hours and len(holdout) >= min_holdout_hours:
            offset = median(p.model_ugm3 - p.observed_ugm3 for p in training)
            baseline, candidate = _metrics(holdout), _metrics(holdout, offset)
            item.update(
                status="evaluated",
                offset_ugm3=offset,
                baseline_holdout=baseline,
                candidate_holdout=candidate,
                improves_holdout=all(candidate[k] < baseline[k] for k in ("mae_ugm3", "rmse_ugm3")),
            )
        report["series"].append(item)
    if any(item["status"] == "evaluated" for item in report["series"]):
        report["status"] = "evaluated"
    return report
