"""Deterministic alert evaluation independent from delivery adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True, slots=True)
class AlertRule:
    subscription_id: str
    location_key: str
    metric: Literal["pm25", "pm10", "outdoor_score"]
    threshold: float
    direction: Literal["above", "below"] = "above"
    quiet_start: time = time(22, 0)
    quiet_end: time = time(6, 0)
    cooldown_minutes: int = 180
    max_data_age_minutes: int = 180


@dataclass(frozen=True, slots=True)
class AlertSnapshot:
    valid_at_utc: datetime
    fetched_at_utc: datetime
    value: float
    source_type: Literal["observed", "modeled"]
    coverage_tier: str


@dataclass(frozen=True, slots=True)
class AlertDecision:
    should_send: bool
    reason: str
    idempotency_key: str | None = None


def _is_quiet(local_time: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def _threshold_reached(rule: AlertRule, value: float) -> bool:
    if rule.direction == "above":
        return value >= rule.threshold
    return value <= rule.threshold


def alert_idempotency_key(rule: AlertRule, snapshot: AlertSnapshot) -> str:
    """Build one stable event key per subscription, metric and valid hour."""

    valid_hour = snapshot.valid_at_utc.replace(minute=0, second=0, microsecond=0).isoformat()
    material = (
        f"{rule.subscription_id}|{rule.location_key}|{rule.metric}|"
        f"{rule.direction}|{rule.threshold}|{valid_hour}|{snapshot.source_type}"
    )
    return hashlib.sha256(material.encode()).hexdigest()


def evaluate_alert(
    rule: AlertRule,
    snapshot: AlertSnapshot,
    *,
    now_utc: datetime,
    last_sent_at_utc: datetime | None = None,
) -> AlertDecision:
    """Evaluate freshness, quiet hours, threshold and cooldown in that order."""

    for name, value in {
        "now_utc": now_utc,
        "valid_at_utc": snapshot.valid_at_utc,
        "fetched_at_utc": snapshot.fetched_at_utc,
    }.items():
        if value.tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware")

    data_age = now_utc - snapshot.fetched_at_utc
    if data_age > timedelta(minutes=rule.max_data_age_minutes):
        return AlertDecision(False, "Dữ liệu quá cũ; cảnh báo bị chặn.")
    if snapshot.coverage_tier in {"NO_DATA", "SOURCE_ERROR", "OBSERVED_STALE"}:
        return AlertDecision(False, f"Coverage {snapshot.coverage_tier} không đủ để cảnh báo.")

    local_now = now_utc.astimezone(VIETNAM_TZ).time().replace(tzinfo=None)
    if _is_quiet(local_now, rule.quiet_start, rule.quiet_end):
        return AlertDecision(False, "Đang trong quiet hours.")
    if not _threshold_reached(rule, snapshot.value):
        return AlertDecision(False, "Chưa đạt ngưỡng cảnh báo.")
    if last_sent_at_utc is not None:
        if last_sent_at_utc.tzinfo is None:
            raise ValueError("last_sent_at_utc must be timezone-aware")
        if now_utc - last_sent_at_utc < timedelta(minutes=rule.cooldown_minutes):
            return AlertDecision(False, "Đang trong cooldown chống gửi trùng.")

    source_label = "quan trắc" if snapshot.source_type == "observed" else "mô hình"
    return AlertDecision(
        True,
        f"Đạt ngưỡng trên dữ liệu {source_label} và đủ mới.",
        alert_idempotency_key(rule, snapshot),
    )
