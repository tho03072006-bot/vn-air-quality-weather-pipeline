"""Failure callbacks shared by Airflow DAGs without importing Airflow itself."""

import logging
from collections.abc import Mapping
from typing import Any

LOGGER = logging.getLogger(__name__)


def log_task_failure(context: Mapping[str, Any]) -> None:
    """Emit one structured failure record without masking the task exception."""

    try:
        task_instance = context.get("task_instance") or context.get("ti")
        dag = context.get("dag")
        task = context.get("task")
        dag_run = context.get("dag_run")
        exception = context.get("exception")
        exception_text = repr(exception).replace("\r", "\\r").replace("\n", "\\n")

        LOGGER.error(
            "airflow_task_failure dag_id=%s task_id=%s run_id=%s "
            "data_interval_start=%s data_interval_end=%s exception=%s",
            _first_value(_attribute(task_instance, "dag_id"), _attribute(dag, "dag_id")),
            _first_value(_attribute(task_instance, "task_id"), _attribute(task, "task_id")),
            _first_value(_attribute(task_instance, "run_id"), _attribute(dag_run, "run_id")),
            _first_value(context.get("data_interval_start")),
            _first_value(context.get("data_interval_end")),
            exception_text,
        )
    except Exception:
        # Callback failures must never replace the original Airflow task failure.
        try:
            LOGGER.exception("airflow_task_failure_callback_error")
        except Exception:
            pass


def _attribute(value: Any, name: str) -> Any:
    return getattr(value, name, None) if value is not None else None


def _first_value(*values: Any) -> str:
    for value in values:
        if value is not None:
            return str(value)
    return "unknown"
