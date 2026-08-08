"""Measure server-side render time for every dashboard page.

What this measures and what it does not, stated up front because a benchmark whose
scope is unclear gets quoted as something it never measured:

  * MEASURED: the time Streamlit spends executing a page script, including the DuckDB
    queries it issues. Cold means the first execution with an empty cache; warm means
    a re-run with `st.cache_data` populated.
  * NOT MEASURED: browser paint, WebGL setup for the PyDeck map, network transfer, or
    Altair rendering in the client. A page that is fast here can still feel slow in a
    browser, and the map is the most likely place for that to happen.

Run against the demo fixture:

    $env:DUCKDB_PATH = (Resolve-Path "data\\warehouse\\verify.duckdb").Path
    python scripts/benchmark_dashboard.py
"""

from __future__ import annotations

import statistics
import time

from streamlit.testing.v1 import AppTest

PAGES = (
    "today.py",
    "forecast.py",
    "national_map.py",
    "compare.py",
    "history.py",
    "custom_location.py",
    "alerts.py",
    "trust.py",
    "pipeline_health.py",
)
WARM_REPEATS = 3


def _time_page(app: AppTest, page: str) -> float:
    started = time.perf_counter()
    app.switch_page(f"app_pages/{page}").run(timeout=120)
    return (time.perf_counter() - started) * 1000.0


def main() -> None:
    app = AppTest.from_file("dashboard/app.py").run(timeout=120)

    print(f"{'page':<24}{'cold (ms)':>12}{'warm median (ms)':>20}{'warm spread':>14}")
    print("-" * 70)
    cold_times: list[float] = []
    warm_medians: list[float] = []

    for page in PAGES:
        cold = _time_page(app, page)
        warm = [_time_page(app, page) for _ in range(WARM_REPEATS)]
        warm_median = statistics.median(warm)
        cold_times.append(cold)
        warm_medians.append(warm_median)
        spread = f"{min(warm):.0f}-{max(warm):.0f}"
        print(f"{page:<24}{cold:>12.0f}{warm_median:>20.0f}{spread:>14}")

    print("-" * 70)
    print(f"{'slowest cold':<24}{max(cold_times):>12.0f}")
    print(f"{'slowest warm median':<24}{'':>12}{max(warm_medians):>20.0f}")
    print(
        "\nScope: server-side script execution only. Browser paint, WebGL map setup and "
        "client-side Altair rendering are not included."
    )


if __name__ == "__main__":
    main()
