"""Print an offline station-analysis shadow report; never read .env or write data.

Example (from the repository root, with the installed project environment):
    python scripts/evaluate_station_calibration.py --database path/to/warehouse.duckdb

Minimum hours and the chronological training fraction are explicit research guards.
Changing them is a sensitivity analysis, never tuning against holdout to select a
public correction. Output metrics concern station analysis only. Exit 2 means an
invalid/unreadable input; insufficient history is a valid readiness report (exit 0).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

from vn_air_quality_weather.calibration import (
    TARGET,
    evaluate_station_calibration,
    load_station_pairs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--train-fraction", type=float, default=0.75)
    parser.add_argument("--min-train-hours", type=int, default=72)
    parser.add_argument("--min-holdout-hours", type=int, default=24)
    args = parser.parse_args()
    try:
        report = evaluate_station_calibration(
            load_station_pairs(args.database),
            train_fraction=args.train_fraction,
            min_train_hours=args.min_train_hours,
            min_holdout_hours=args.min_holdout_hours,
        )
    except (ValueError, OSError, duckdb.Error) as error:
        # DuckDB errors can contain SQL/file locations but these inputs contain no
        # credentials: this entry point never constructs Settings or calls an API.
        print(
            json.dumps(
                {"target": TARGET, "status": "invalid_or_unreadable_data", "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
