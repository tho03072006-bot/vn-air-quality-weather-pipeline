"""Presentation logic with no Streamlit import, so it can be unit tested directly.

Everything here turns warehouse values into strings a person reads. It lives apart
from the components because formatting is where the display bugs were: the Today
page formatted `f"{row['pm25_ugm3']:.1f}"` straight off the row, and the serving
mart left-joins weather and explicitly tests for those columns being null, so a
missing value rendered as the literal text "nan µg/m³" -- or raised TypeError when
the column carried None rather than NaN.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

# An em dash reads as "no value" without pretending to be one. "0" and "N/A" both
# invite being mistaken for data.
MISSING_DISPLAY = "—"

POLLUTANT_LABELS = {
    "pm25": "PM2.5",
    "pm10": "PM10",
    "no2": "NO₂",
    "o3": "O₃",
    "so2": "SO₂",
    "co": "CO",
}

COVERAGE_LABELS = {
    "OBSERVED_RECENT": "Quan trắc mới",
    "OBSERVED_DELAYED": "Quan trắc trễ",
    "OBSERVED_STALE": "Quan trắc cũ",
    "MODELED_ONLY": "Chỉ có mô hình",
    "NO_DATA": "Không có dữ liệu",
    "SOURCE_ERROR": "Lỗi nguồn",
}

FRESHNESS_LABELS = {
    "FRESH": ("Dữ liệu mới", ":material/check_circle:", "green"),
    "DELAYED": ("Dữ liệu chậm", ":material/schedule:", "orange"),
    "STALE": ("Dữ liệu cũ", ":material/warning:", "red"),
}


def is_missing(value: object) -> bool:
    """True for None, NaN, or the pandas NA singletons.

    Deliberately does not import pandas: NaN is detectable through float, and
    pandas.NA compares unequal to itself in the same way, so a duck-typed check
    keeps this module free of the heavy dependency and of Streamlit.
    """

    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    # pandas.NaT and pandas.NA are both unequal to themselves, but they differ in
    # how they say so: NaT returns a plain True, while NA propagates itself and
    # then refuses bool(). That refusal is the signal, so it must not be swallowed
    # as "present" the way a first pass at this did.
    try:
        not_equal = value != value
    except (TypeError, ValueError):
        return False
    try:
        return bool(not_equal)
    except (TypeError, ValueError):
        return True


def format_number(value: object, *, unit: str = "", decimals: int = 1) -> str:
    """Format a measurement, or return the missing marker rather than "nan"."""

    if is_missing(value):
        return MISSING_DISPLAY
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return MISSING_DISPLAY
    if math.isinf(numeric):
        return MISSING_DISPLAY
    rendered = f"{numeric:.{decimals}f}"
    return f"{rendered} {unit}".strip() if unit else rendered


def format_age(minutes: object) -> str:
    """Render an age in minutes the way a person would say it."""

    if is_missing(minutes):
        return "chưa xác định"
    try:
        total = int(float(minutes))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "chưa xác định"
    if total < 0:
        # The nearest forecast hour can sit ahead of now; that is not staleness.
        return "chưa tới"
    if total < 60:
        return f"{total} phút"
    hours, remainder = divmod(total, 60)
    if hours < 24:
        return f"{hours} giờ {remainder} phút" if remainder else f"{hours} giờ"
    days, leftover_hours = divmod(hours, 24)
    return f"{days} ngày {leftover_hours} giờ" if leftover_hours else f"{days} ngày"


@dataclass(frozen=True, slots=True)
class MetricView:
    """One KPI tile, already reduced to display strings."""

    label: str
    value: str
    help_text: str | None = None

    @property
    def is_available(self) -> bool:
        return self.value != MISSING_DISPLAY


def build_metric(
    label: str,
    value: object,
    *,
    unit: str = "",
    decimals: int = 1,
    help_text: str | None = None,
) -> MetricView:
    """Build a KPI tile, keeping the unit out of the value.

    A four-column KPI row is narrow, and Streamlit truncates an overflowing metric
    value with an ellipsis rather than wrapping it. With the unit inside the value,
    "75.7 µg/m³" rendered as "75…" -- the number itself, the single most important
    thing on the page, was the part that got cut. The unit moves into the label.

    That was originally justified by "the label wraps". It does not: Streamlit gives
    the label `white-space: nowrap` and an ellipsis too, so three labels were still
    being cut at 1280px until `app.py` overrode it. Keep labels short anyway -- the
    override buys a second line, not unlimited room.
    """

    rendered = format_number(value, decimals=decimals)
    if rendered == MISSING_DISPLAY and help_text is None:
        help_text = "Nguồn không trả về giá trị cho giờ này."
    # A unit that already reads as a suffix ("/100") is joined without a space.
    if unit:
        label = f"{label} ({unit})" if not unit.startswith("/") else f"{label} {unit}"
    return MetricView(label=label, value=rendered, help_text=help_text)


def dominant_pollutant(values: dict[str, object]) -> str | None:
    """Return the pollutant with the highest concentration, ignoring missing ones.

    Comparing raw concentrations across pollutants says which number is largest,
    not which is most harmful -- the VN_AQI sub-index does that. Callers must label
    this as the highest concentration, never as the driving pollutant.
    """

    present = {
        pollutant: float(value)  # type: ignore[arg-type]
        for pollutant, value in values.items()
        if not is_missing(value)
    }
    if not present:
        return None
    return max(present, key=lambda key: present[key])


def freshness_view(status: object, age_minutes: object) -> tuple[str, str, str]:
    """Return (text, icon, colour) so freshness never depends on colour alone."""

    key = str(status) if not is_missing(status) else "STALE"
    label, icon, color = FRESHNESS_LABELS.get(key, FRESHNESS_LABELS["STALE"])
    return f"{label} · lấy cách đây {format_age(age_minutes)}", icon, color


# --- Map colour scales -------------------------------------------------------
#
# Bands are data, not branching, so the legend and the marker colours are read
# from one source. A map whose legend is written separately from its fill colours
# drifts the moment a threshold changes, and the reader has no way to notice.

# Grey, and deliberately not on any band's ramp: an anchor with no reading must not
# look like the low end of the scale.
MISSING_RGB: tuple[int, int, int] = (148, 163, 184)


@dataclass(frozen=True, slots=True)
class ColourBand:
    label: str
    upper: float | None  # None means open-ended, so the ramp always terminates.
    rgb: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class MapMetric:
    key: str
    column: str
    label: str
    unit: str
    decimals: int
    bands: tuple[ColourBand, ...]
    legend_note: str
    higher_is_worse: bool = True


# PM2.5 uses the concentration breakpoints of Bang 2, Quyet dinh 1459/QD-TCMT.
# These are the µg/m³ cut points, not the AQI index value -- the legend says so,
# because colouring a concentration with familiar AQI colours otherwise invites
# being read as the official index.
PM25_METRIC = MapMetric(
    key="pm25",
    column="pm25_ugm3",
    label="PM2.5 mô hình",
    unit="µg/m³",
    decimals=1,
    bands=(
        ColourBand("0–25", 25.0, (22, 163, 74)),
        ColourBand("25–50", 50.0, (250, 204, 21)),
        ColourBand("50–80", 80.0, (249, 115, 22)),
        ColourBand("80–150", 150.0, (220, 38, 38)),
        ColourBand("150–250", 250.0, (126, 34, 206)),
        ColourBand("> 250", None, (127, 29, 29)),
    ),
    legend_note=(
        "Ngưỡng nồng độ µg/m³ theo Bảng 2 QĐ 1459/QĐ-TCMT. "
        "Đây là nồng độ, không phải giá trị VN_AQI."
    ),
)

# Thresholds match the decision_label cut points in mart_location_hourly_forecast,
# so the colour and the words on the same row cannot disagree.
OUTDOOR_METRIC = MapMetric(
    key="outdoor_score",
    column="outdoor_score",
    label="Điểm phù hợp ngoài trời",
    unit="/100",
    decimals=0,
    bands=(
        ColourBand("Nên hạn chế (< 45)", 45.0, (220, 38, 38)),
        ColourBand("Cân nhắc (45–70)", 70.0, (250, 204, 21)),
        ColourBand("Phù hợp hơn (≥ 70)", None, (22, 163, 74)),
    ),
    legend_note="Heuristic lập kế hoạch, không phải VN_AQI và không phải chỉ số y tế.",
    higher_is_worse=False,
)

TEMPERATURE_METRIC = MapMetric(
    key="temperature",
    column="temperature_2m_c",
    label="Nhiệt độ",
    unit="°C",
    decimals=1,
    bands=(
        ColourBand("< 20", 20.0, (59, 130, 246)),
        ColourBand("20–27", 27.0, (22, 163, 74)),
        ColourBand("27–32", 32.0, (250, 204, 21)),
        ColourBand("32–36", 36.0, (249, 115, 22)),
        ColourBand("≥ 36", None, (220, 38, 38)),
    ),
    legend_note="Nhiệt độ không khí tại điểm đại diện.",
)

RAIN_METRIC = MapMetric(
    key="rain",
    column="precipitation_probability_pct",
    label="Khả năng mưa",
    unit="%",
    decimals=0,
    bands=(
        ColourBand("0–20", 20.0, (226, 232, 240)),
        ColourBand("20–50", 50.0, (147, 197, 253)),
        ColourBand("50–80", 80.0, (59, 130, 246)),
        ColourBand("≥ 80", None, (30, 64, 175)),
    ),
    legend_note="Xác suất mưa do mô hình dự báo cho giờ đang hiển thị.",
)

MAP_METRICS: tuple[MapMetric, ...] = (
    PM25_METRIC,
    OUTDOOR_METRIC,
    TEMPERATURE_METRIC,
    RAIN_METRIC,
)


def band_for(value: object, metric: MapMetric) -> ColourBand | None:
    """Return the band a value falls in, or None when there is no value.

    Upper bounds are exclusive so a value sitting exactly on a threshold lands in
    the band whose label starts with it, matching how the ranges read.
    """

    if is_missing(value):
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    for band in metric.bands:
        if band.upper is None or numeric < band.upper:
            return band
    return metric.bands[-1]


def band_colour(value: object, metric: MapMetric) -> tuple[int, int, int]:
    band = band_for(value, metric)
    return MISSING_RGB if band is None else band.rgb


def marker_radius(value: object, metric: MapMetric) -> int:
    """Scale the marker by which band the value sits in, not by the raw number.

    Sizing by raw value made one polluted anchor swamp the map, and gave every
    anchor a different radius for differences too small to mean anything. Banding
    the radius keeps it an aid to reading the colour rather than a second,
    contradictory scale.
    """

    band = band_for(value, metric)
    if band is None:
        return 9000
    position = metric.bands.index(band)
    severity = position if metric.higher_is_worse else len(metric.bands) - 1 - position
    return 12000 + severity * 6000


# One colour per pollutant, reused by every chart in the app. A pollutant that is
# blue on one page and orange on the next forces the reader to re-learn the legend
# each time, and makes two charts impossible to compare at a glance.
POLLUTANT_COLOURS = {
    "pm25": "#b91c1c",
    "pm10": "#ea580c",
    "no2": "#7c3aed",
    "o3": "#0891b2",
    "so2": "#a16207",
    "co": "#4b5563",
}

# Concentration columns in the serving mart, with the label and colour to draw them
# with. Order is the order they appear on the page.
POLLUTANT_SERIES = (
    ("pm25", "pm25_ugm3"),
    ("pm10", "pm10_ugm3"),
    ("no2", "no2_ugm3"),
    ("o3", "o3_ugm3"),
    ("so2", "so2_ugm3"),
    ("co", "co_ugm3"),
)

# The first PM2.5 breakpoint of Bang 2. Drawn as a reference line so a reader can
# see whether a curve crosses it without reading values off the axis.
PM25_REFERENCE_UGM3 = 25.0


def normalise_datetimes(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy whose datetime columns are nanosecond-precision.

    DuckDB hands pandas datetime64[us], and Vega cannot read the way Altair
    serialises that unit: every timestamp arrives unparseable, so the axis domain
    collapses to [Infinity, -Infinity] and the chart draws nothing. It fails
    silently -- Vega logs a warning to the browser console and renders an empty
    plot, so a server-side test that only checks the surrounding caption still
    passes. pd.to_datetime does not fix it either; the unit has to be cast.

    Timezone-aware columns keep their zone, because the pages deliberately show
    Vietnam local time.
    """

    converted = frame.copy()
    for column in converted.columns:
        dtype = converted[column].dtype
        if pd.api.types.is_datetime64_any_dtype(dtype) and getattr(dtype, "unit", None) != "ns":
            converted[column] = converted[column].astype(
                pd.DatetimeTZDtype(tz=dtype.tz) if getattr(dtype, "tz", None) else "datetime64[ns]"
            )
    return converted


HOURS_PER_DAY = 24


def build_coverage(
    frame: pd.DataFrame,
    start_date: date,
    end_date: date,
    *,
    locations: Sequence[str] | None = None,
    location_column: str = "Tỉnh/thành",
    timestamp_column: str = "observed_at_utc",
) -> pd.DataFrame:
    """Hours present per location per UTC day, across the *whole* selected range.

    The obvious implementation -- group by day and count distinct hours -- can only
    ever describe days that have at least one row. A day with no data produces no
    group, so it vanishes from the result, and the strip whose entire purpose is to
    show missing data was blind to the worst case it could report. Measured against
    the real warehouse: PM2.5 observed over 2026-07-27..2026-08-07 returned four
    groups, all reading a full 24 hours, and the page concluded "every day in the
    selected range has a full 24 hours" while ten of the twelve days held no rows at
    all. Nothing raised, so nothing caught it.

    Reindexing over the full date range makes an absent *day* explicit: zero hours
    present, twenty-four missing.

    The location axis has exactly the same failure mode one dimension over, and
    deriving it from the frame does not fix it. Pass ``locations`` -- what the reader
    actually selected -- or a location with no rows anywhere in the range is dropped
    from the strip entirely rather than reported as empty. Measured against the real
    warehouse on the *default* History filters (all three cities, PM2.5, observed):
    Da Nang has no observed PM2.5 rows at all, so the frame-derived axis published a
    coverage strip for two cities and never mentioned the third -- the one with the
    worst coverage of the three. Falling back to the frame is kept only for callers
    that have no selection to pass.
    """

    days = pd.date_range(start_date, end_date, freq="D").date
    if locations is None:
        axis = [] if frame.empty else sorted(frame[location_column].dropna().unique())
    else:
        axis = sorted({str(location) for location in locations})

    if not axis or len(days) == 0:
        return pd.DataFrame(
            columns=[location_column, "data_date_utc", "hours_with_data", "missing_hours"]
        )

    grid = pd.MultiIndex.from_product([axis, days], names=[location_column, "data_date_utc"])
    if frame.empty:
        counted = pd.Series(0, index=grid, dtype=int)
    else:
        observed = pd.to_datetime(frame[timestamp_column], utc=True)
        counted = (
            frame.assign(data_date_utc=observed.dt.date)
            .groupby([location_column, "data_date_utc"])[timestamp_column]
            .nunique()
        )
    coverage = (
        counted.reindex(grid, fill_value=0)
        .reset_index(name="hours_with_data")
        .astype({"hours_with_data": int})
    )
    coverage["missing_hours"] = (HOURS_PER_DAY - coverage["hours_with_data"]).clip(lower=0)
    return coverage.sort_values([location_column, "data_date_utc"], ignore_index=True)


@dataclass(frozen=True, slots=True)
class ActivityPriority:
    """How to rank locations for a given outing.

    Deliberately a sort order over columns that already exist, not a new score.
    Inventing a per-activity index would add four more unvalidated heuristics on top
    of outdoor_score, which the register already flags as a planning aid rather than
    a health measure. Saying "ranked by PM2.5, then by apparent temperature" is a
    claim the data supports; "your running score is 72" is not.
    """

    key: str
    label: str
    explanation: str
    # Columns in priority order, paired with whether a lower value ranks better.
    sort_columns: tuple[tuple[str, bool], ...]


ACTIVITY_PRIORITIES: tuple[ActivityPriority, ...] = (
    ActivityPriority(
        key="general",
        label="Chung",
        explanation="Xếp theo điểm phù hợp ngoài trời tổng hợp.",
        sort_columns=(("outdoor_score", False),),
    ),
    ActivityPriority(
        key="running",
        label="Chạy bộ",
        explanation=(
            "Ưu tiên PM2.5 thấp trước, vì gắng sức làm tăng thể tích khí hít vào, "
            "rồi tới cảm giác nhiệt."
        ),
        sort_columns=(("pm25_ugm3", True), ("apparent_temperature_c", True)),
    ),
    ActivityPriority(
        key="walking",
        label="Đi bộ",
        explanation="Ưu tiên cảm giác nhiệt dễ chịu, rồi tới PM2.5.",
        sort_columns=(("apparent_temperature_c", True), ("pm25_ugm3", True)),
    ),
    ActivityPriority(
        key="outdoor_event",
        label="Sự kiện ngoài trời",
        explanation="Ưu tiên ít mưa nhất, rồi tới PM2.5.",
        sort_columns=(("precipitation_probability_pct", True), ("pm25_ugm3", True)),
    ),
)


def activity_by_key(key: str) -> ActivityPriority:
    for activity in ACTIVITY_PRIORITIES:
        if activity.key == key:
            return activity
    return ACTIVITY_PRIORITIES[0]


def pollutant_colour(pollutant: str) -> str:
    return POLLUTANT_COLOURS.get(pollutant, "#4b5563")


def pollutant_label(pollutant: str) -> str:
    return POLLUTANT_LABELS.get(pollutant, pollutant.upper())


def metric_by_key(key: str) -> MapMetric:
    for metric in MAP_METRICS:
        if metric.key == key:
            return metric
    return PM25_METRIC
