from datetime import UTC, datetime, time, timedelta

import pytest

from vn_air_quality_weather.alerts import AlertRule, AlertSnapshot, evaluate_alert

NOW = datetime(2026, 8, 2, 5, 0, tzinfo=UTC)  # 12:00 in Vietnam


def snapshot(**overrides) -> AlertSnapshot:
    values = {
        "valid_at_utc": NOW,
        "fetched_at_utc": NOW - timedelta(minutes=10),
        "value": 55.0,
        "source_type": "modeled",
        "coverage_tier": "MODELED_ONLY",
    }
    values.update(overrides)
    return AlertSnapshot(**values)


def test_alert_is_deterministic_and_labels_modeled_source() -> None:
    rule = AlertRule("sub-1", "hanoi", "pm25", 35.0)
    first = evaluate_alert(rule, snapshot(), now_utc=NOW)
    second = evaluate_alert(rule, snapshot(), now_utc=NOW)

    assert first.should_send
    assert first.idempotency_key == second.idempotency_key
    assert "mô hình" in first.reason


@pytest.mark.parametrize(
    ("changed_snapshot", "last_sent", "expected_reason"),
    [
        ({"fetched_at_utc": NOW - timedelta(hours=4)}, None, "quá cũ"),
        ({"coverage_tier": "SOURCE_ERROR"}, None, "SOURCE_ERROR"),
        ({"value": 20.0}, None, "Chưa đạt"),
        ({}, NOW - timedelta(minutes=30), "cooldown"),
    ],
)
def test_alert_suppression(
    changed_snapshot: dict[str, object],
    last_sent: datetime | None,
    expected_reason: str,
) -> None:
    decision = evaluate_alert(
        AlertRule("sub-1", "hanoi", "pm25", 35.0),
        snapshot(**changed_snapshot),
        now_utc=NOW,
        last_sent_at_utc=last_sent,
    )
    assert not decision.should_send
    assert expected_reason in decision.reason


def test_quiet_hours_cross_midnight() -> None:
    local_23h = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
    decision = evaluate_alert(
        AlertRule("sub-1", "hanoi", "pm25", 35.0, quiet_start=time(22), quiet_end=time(6)),
        snapshot(fetched_at_utc=local_23h),
        now_utc=local_23h,
    )
    assert not decision.should_send
    assert decision.reason == "Đang trong quiet hours."
