# Station-local calibration evaluation

This experiment is separate from the published forecast and outdoor decision score.
It evaluates historical CAMS analysis requested at a station's coordinates against
that station's own hourly OpenAQ observations. A model grid cell is still not an
instrument at a street location. A successful experiment here is not a measurement
of forecast accuracy and cannot justify correcting every province anchor.

## Run the read-only report

From the pipeline repository, using its installed Python environment:

```powershell
python scripts/evaluate_station_calibration.py `
    --database data/warehouse/vn_air_quality_weather.duckdb
```

The database path is mandatory. The command does not construct Settings, read
`.env`, call an API, write a model, publish to SQL Server or alter the warehouse.
It prints JSON to stdout. Exit 0 means the report was produced, including a valid
`not_ready` or `insufficient_data` result; it does not mean a candidate improved.
Exit 2 means the input is invalid or unreadable.

The loader joins `raw.air_quality_at_station_hourly` with OpenAQ rows from
`raw.air_quality_hourly` on station, pollutant and exact UTC hour. It rejects
ambiguous duplicates and changing station positions, checks coordinate metadata and
units, and excludes flagged, missing or invalid readings. It never substitutes a
city average or province-anchor forecast for a missing station sample. Older raw
rows without station coordinates remain unavailable rather than being rewritten.

## What the candidate does

One chronological boundary splits distinct UTC hours across all stations. The
candidate fits each station/pollutant's median signed model-observation gap on the
earlier training part, subtracts that offset, and clamps the resulting concentration
at zero. Later holdout observations do not participate in fitting.

Default experimental guards are a training fraction of 0.75, at least 72 training
hours and at least 24 holdout hours per series. They are configurable with
`--train-fraction`, `--min-train-hours` and `--min-holdout-hours`; they are not product
thresholds or evidence of statistical reliability. Changing them after examining
holdout performance does not create independent validation.

Only a series with enough samples receives diagnostic MAE, RMSE and signed-gap
metrics. `improves_holdout` requires both held-out MAE and RMSE to decrease. It is a
station-local experimental result, not permission to deploy a correction. The
original forecasts, confidence labels, VN_AQI and outdoor-score cut points remain
unchanged.

Time ordering matters because ordinary shuffled splits can train on future data
and evaluate on the past; this is the motivation documented for
[TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
The implementation uses the standard library and adds no scikit-learn dependency.
ECMWF also documents that locally influenced station measurements can be
unrepresentative of CAMS grid scale in its
[observation classification work](https://codeforearth.ecmwf.int/project/air-quality-observation-classification/).

## Measured readiness on 11 October 2026

The real warehouse read returned `not_ready`: the station-analysis raw table was
absent, with 0 model rows, 956 observation rows and 0 matched pairs. No diagnostic
metrics or improvement claim were produced. The CLI distinguishes this result from
the synthetic fixtures that test the algorithm and its leakage guards.

The existing historical pipeline already samples CAMS at selected stations when
OpenAQ provides coordinates. To acquire the missing inputs, run historical
ingestion through the normal warehouse-writer path with working OpenAQ credentials;
do not run an independent writer alongside Airflow. Preserve each original raw
envelope and its fetch time.

After sufficient paired history exists, evaluate a predeclared chronological holdout
and additional forward windows. Real forecast calibration needs forecasts archived
at those same station coordinates before their valid hours, with issue time and
lead band retained. Historical analysis alone does not supply that record. No such
forecast-calibration result is claimed here.

## Verification

```powershell
python -m pytest tests/test_calibration.py
```

Regression tests include reversed holdout offsets, quality/unit/coordinate problems,
duplicate hours, unknown schemas, negative-value clamping and future-data leakage.
The leakage fixture varies future observations while asserting that the fitted
training offset stays identical. Restoring a fit over all hours fails that test.
Replacing the median with a mean fails the outlier-resistance test.
