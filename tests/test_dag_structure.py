"""Mostly-static checks on the DAG files.

Airflow is not a project dependency -- it exists only in the container image -- so
these parse the DAG source instead of importing it. That is a real limitation: it
verifies the declarations, not that Airflow accepts them. The DagBag import test
run inside the Airflow image covers the other half. The daily-timetable regression
also exercises Airflow's implementation when this file runs in that image.
"""

import ast
from pathlib import Path

import pytest

from vn_air_quality_weather.settings import WAREHOUSE_WRITER_POOL

DAG_DIR = Path(__file__).resolve().parents[1] / "airflow" / "dags"
# Tasks that open the DuckDB file or shell out to dbt. DuckDB serialises writers,
# so each of these has to hold the single-slot pool or two DAGs can collide.
WAREHOUSE_WRITING_TASKS = {
    "vn_air_quality_weather_daily.py": {"extract_store_and_load", "build_analytics"},
    "vn_air_quality_weather_forecast.py": {"ingest_forecast", "build_analytics"},
}


def _dag_schedule(dag_path: Path) -> ast.expr:
    """Return the value assigned to ``schedule`` on the file's ``@dag``."""

    tree = ast.parse(dag_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "dag"
            ):
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "schedule":
                    return keyword.value
    raise AssertionError(f"{dag_path.name} has no @dag schedule")


def test_daily_timetable_targets_the_previous_completed_day() -> None:
    """A run after 2026-08-14 02:00 UTC must process 2026-08-13."""

    schedule = _dag_schedule(DAG_DIR / "vn_air_quality_weather_daily.py")
    assert isinstance(schedule, ast.Call), (
        "daily must use CronDataIntervalTimetable; a bare cron string becomes "
        "CronTriggerTimetable and gives the task a zero-length interval"
    )
    assert isinstance(schedule.func, ast.Name)
    assert schedule.func.id == "CronDataIntervalTimetable"
    assert len(schedule.args) == 1
    assert ast.literal_eval(schedule.args[0]) == "0 2 * * *"
    assert {keyword.arg: ast.literal_eval(keyword.value) for keyword in schedule.keywords} == {
        "timezone": "UTC"
    }

    # Host-side tests intentionally do not install Airflow. In the Airflow image,
    # exercise the actual timetable as well as guarding its source declaration.
    try:
        from airflow.timetables.interval import CronDataIntervalTimetable
    except ModuleNotFoundError:
        return

    import pendulum

    timetable = CronDataIntervalTimetable("0 2 * * *", timezone="UTC")
    interval = timetable.infer_manual_data_interval(
        run_after=pendulum.datetime(2026, 8, 14, 2, 0, tz="UTC")
    )
    assert interval.start.date().isoformat() == "2026-08-13"


def _task_pools(dag_path: Path) -> dict[str, str | None]:
    """Map each @task-decorated function to the pool it declares, if any."""

    tree = ast.parse(dag_path.read_text(encoding="utf-8"))
    pools: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            is_bare_task = isinstance(decorator, ast.Name) and decorator.id == "task"
            is_called_task = (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "task"
            )
            if not (is_bare_task or is_called_task):
                continue
            pool: str | None = None
            if is_called_task:
                for keyword in decorator.keywords:
                    if keyword.arg == "pool":
                        # The DAGs reference the shared constant rather than a
                        # literal, so resolve the name back to its value.
                        if isinstance(keyword.value, ast.Name):
                            pool = WAREHOUSE_WRITER_POOL
                        elif isinstance(keyword.value, ast.Constant):
                            pool = str(keyword.value.value)
            pools[node.name] = pool
    return pools


@pytest.mark.parametrize("dag_file", sorted(WAREHOUSE_WRITING_TASKS))
def test_every_warehouse_writing_task_holds_the_single_writer_pool(dag_file: str) -> None:
    pools = _task_pools(DAG_DIR / dag_file)
    for task_name in WAREHOUSE_WRITING_TASKS[dag_file]:
        assert task_name in pools, f"{dag_file} no longer defines {task_name}"
        assert pools[task_name] == WAREHOUSE_WRITER_POOL, (
            f"{dag_file}:{task_name} writes the warehouse without the "
            f"{WAREHOUSE_WRITER_POOL} pool, so it can run concurrently with the "
            "other DAG and hit a DuckDB write lock"
        )


@pytest.mark.parametrize("dag_file", sorted(WAREHOUSE_WRITING_TASKS))
def test_read_only_tasks_do_not_hold_the_pool(dag_file: str) -> None:
    # Holding a one-slot pool for work that does not touch the warehouse would
    # serialise the pipeline for no reason.
    pools = _task_pools(DAG_DIR / dag_file)
    for task_name, pool in pools.items():
        if task_name in WAREHOUSE_WRITING_TASKS[dag_file]:
            continue
        assert pool is None, f"{dag_file}:{task_name} holds the writer pool but does not write"


def test_the_pool_is_provisioned_by_compose() -> None:
    # A pool referenced by a task but never created leaves the task queued forever,
    # which looks like a hung scheduler rather than a configuration error.
    compose = (DAG_DIR.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"airflow pools set {WAREHOUSE_WRITER_POOL} 1" in compose


def _dbt_project_roots(dag_path: Path) -> set[str]:
    """Every literal path handed to run_dbt_build as project_root."""

    tree = ast.parse(dag_path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "run_dbt_build"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "project_root":
                continue
            # Path("/opt/project") -- the container convention, not a real
            # directory on any developer machine.
            value = keyword.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "Path"
                and value.args
                and isinstance(value.args[0], ast.Constant)
            ):
                roots.add(str(value.args[0].value))
    return roots


@pytest.mark.parametrize("dag_file", sorted(WAREHOUSE_WRITING_TASKS))
def test_dbt_project_root_is_actually_mounted_by_compose(dag_file: str) -> None:
    """The DAG's hard-coded container path must match what compose provides.

    `run_dbt_build(project_root=Path("/opt/project"))` reads `<root>/dbt` for both
    `--project-dir` and `--profiles-dir`. That directory exists only because
    docker-compose bind-mounts `../dbt` there -- the image never copies it. So the
    DAG depends on a path that is declared in a different file, in a different
    language, with nothing tying the two together.

    This is the same failure shape the pool test already guards: a DAG referencing
    infrastructure that may not exist. Renaming the mount, or dropping it while
    relying on the image, leaves the DAG importable and every unit test green, and
    fails only at runtime inside `build_analytics` when dbt cannot find the project.
    """

    roots = _dbt_project_roots(DAG_DIR / dag_file)
    assert roots, f"{dag_file} no longer calls run_dbt_build with an explicit project_root"

    compose = (DAG_DIR.parent / "docker-compose.yml").read_text(encoding="utf-8")
    for root in roots:
        assert f":{root}/dbt" in compose, (
            f"{dag_file} runs dbt against {root}/dbt, but docker-compose.yml mounts "
            f"nothing there. dbt would fail with a missing project at runtime."
        )
        assert f":{root}/src" in compose, (
            f"{dag_file} declares {root} as the project root, but compose does not "
            f"mount src there -- library code and DAG code would drift apart again."
        )


def test_dags_do_not_read_wall_clock_time_for_their_data_window() -> None:
    # A DAG that derives its window from now() cannot backfill: a run for an
    # interval three months ago must produce what it would have produced then.
    for dag_file in WAREHOUSE_WRITING_TASKS:
        source = (DAG_DIR / dag_file).read_text(encoding="utf-8")
        assert "datetime.now(" not in source, f"{dag_file} reads wall-clock time"
        assert "date.today(" not in source, f"{dag_file} reads wall-clock time"
