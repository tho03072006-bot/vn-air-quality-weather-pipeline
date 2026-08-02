import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from vn_air_quality_weather.airflow_callbacks import log_task_failure


def test_log_task_failure_emits_structured_context(caplog: Any) -> None:
    context = {
        "task_instance": SimpleNamespace(
            dag_id="vn_air_quality_weather_daily",
            task_id="extract_store_and_load",
            run_id="scheduled__2026-08-01T02:00:00+00:00",
        ),
        "data_interval_start": datetime(2026, 8, 1, tzinfo=UTC),
        "data_interval_end": datetime(2026, 8, 2, tzinfo=UTC),
        "exception": ValueError("upstream\nresponse was invalid"),
    }

    with caplog.at_level(
        logging.ERROR,
        logger="vn_air_quality_weather.airflow_callbacks",
    ):
        log_task_failure(context)

    message = caplog.messages[-1]
    assert "airflow_task_failure" in message
    assert "dag_id=vn_air_quality_weather_daily" in message
    assert "task_id=extract_store_and_load" in message
    assert "run_id=scheduled__2026-08-01T02:00:00+00:00" in message
    assert "data_interval_start=2026-08-01 00:00:00+00:00" in message
    assert "data_interval_end=2026-08-02 00:00:00+00:00" in message
    assert "exception=ValueError('upstream\\nresponse was invalid')" in message
    assert "\n" not in message


def test_log_task_failure_never_raises_for_invalid_context() -> None:
    log_task_failure(None)  # type: ignore[arg-type]
