"""Shared Streamlit runtime, cached queries, and presentation helpers."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.components import freshness_badge, methodology_expander, source_badges
from dashboard.data_access import (
    load_current_conditions,
    load_decision_windows,
    load_location_forecast,
    load_pipeline_health,
    load_pipeline_runs,
    load_provinces,
    relation_exists,
)
from dashboard.view_models import COVERAGE_LABELS, POLLUTANT_LABELS
from vn_air_quality_weather.clients.geocoding import OpenMeteoGeocodingClient
from vn_air_quality_weather.clients.open_meteo import OpenMeteoClient
from vn_air_quality_weather.on_demand import CustomLocation, fetch_on_demand_forecast
from vn_air_quality_weather.settings import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "warehouse" / "vn_air_quality_weather.duckdb"

# Re-exported so the pages that already import these names from runtime keep
# working. The definitions live in view_models, which imports no Streamlit and is
# therefore unit tested; duplicating them here is how the two copies drifted apart
# in the first place.
__all__ = [
    "COVERAGE_LABELS",
    "POLLUTANT_LABELS",
    "freshness_badge",
    "source_badges",
]


def database_path() -> Path:
    """Resolve the active DuckDB path once for every page."""

    return Path(os.environ.get("DUCKDB_PATH", DEFAULT_DATABASE_PATH))


def require_warehouse(*relations: str) -> Path:
    """Render an actionable empty state when the warehouse is not ready."""

    path = database_path()
    if not path.exists():
        st.error(
            "Chưa có warehouse. Hãy chạy `python scripts/build_demo_warehouse.py` "
            "và `dbt build` trước.",
            icon=":material/database_off:",
        )
        st.stop()
    missing = [name for name in relations if not cached_relation_exists(str(path), name)]
    if missing:
        st.warning(
            "Warehouse chưa có các mart mới: " + ", ".join(missing) + ". Hãy chạy `dbt build`.",
            icon=":material/build:",
        )
        st.stop()
    return path


@st.cache_data(ttl="5m", max_entries=32)
def cached_relation_exists(path: str, relation: str) -> bool:
    return relation_exists(Path(path), "analytics", relation)


@st.cache_data(ttl="30m", max_entries=4)
def cached_provinces(path: str) -> pd.DataFrame:
    return load_provinces(Path(path))


@st.cache_data(ttl="5m", max_entries=64)
def cached_current(path: str, location_key: str | None = None) -> pd.DataFrame:
    return load_current_conditions(Path(path), location_key)


@st.cache_data(ttl="5m", max_entries=128)
def cached_forecast(path: str, location_key: str, hours: int = 72) -> pd.DataFrame:
    return load_location_forecast(Path(path), location_key, hours)


@st.cache_data(ttl="5m", max_entries=128)
def cached_windows(path: str, location_key: str, limit: int = 5) -> pd.DataFrame:
    return load_decision_windows(Path(path), location_key, limit)


@st.cache_data(ttl="5m", max_entries=4)
def cached_pipeline_health(path: str) -> pd.DataFrame:
    return load_pipeline_health(Path(path))


@st.cache_data(ttl="5m", max_entries=4)
def cached_pipeline_runs(path: str) -> pd.DataFrame:
    return load_pipeline_runs(Path(path))


@st.cache_data(ttl="1h", max_entries=64, show_spinner=False)
def cached_location_search(query: str, count: int = 10) -> tuple[dict[str, object], ...]:
    """Search Vietnam locations with a bounded cache around the public API."""

    settings = get_settings()
    with OpenMeteoGeocodingClient(
        timeout_seconds=settings.http_timeout_seconds,
        retry_policy=settings.retry_policy(),
    ) as client:
        rows: list[dict[str, object]] = []
        for result in client.search(query, count=count):
            row = asdict(result)
            row["display_label"] = result.display_label
            rows.append(row)
        return tuple(rows)


@st.cache_data(ttl="15m", max_entries=128, show_spinner=False)
def cached_on_demand_forecast(
    latitude: float,
    longitude: float,
    hours: int = 72,
) -> pd.DataFrame:
    """Fetch one temporary coordinate forecast without writing to DuckDB."""

    settings = get_settings()
    location = CustomLocation(
        display_name="Địa điểm tùy chọn",
        latitude=latitude,
        longitude=longitude,
    )
    with OpenMeteoClient(
        timeout_seconds=settings.http_timeout_seconds,
        retry_policy=settings.retry_policy(),
    ) as client:
        records = fetch_on_demand_forecast(location, forecast_hours=hours, client=client)
    return pd.DataFrame(asdict(record) for record in records)


def province_options(path: Path) -> tuple[list[str], dict[str, str]]:
    provinces = cached_provinces(str(path))
    keys = provinces["province_key"].tolist()
    labels = dict(zip(provinces["province_key"], provinces["province_name"], strict=True))
    return keys, labels


def choose_province(path: Path, key: str, label: str = "Tỉnh/thành") -> str:
    """Render a consistent province selector and preserve the cross-page choice."""

    keys, labels = province_options(path)
    default = st.session_state.get("selected_province", "hanoi")
    index = keys.index(default) if default in keys else 0
    selection = st.selectbox(
        label,
        keys,
        index=index,
        format_func=lambda value: labels.get(value, value),
        key=key,
    )
    st.session_state.selected_province = selection
    return selection


def primary_location(path: Path) -> str:
    """Return the location the header selected, without drawing a second selector.

    The header owns this choice. Before this existed, every single-location page also
    called choose_province, so the app rendered two location controls stacked on top of
    each other and it was not obvious which one was authoritative.
    """

    keys, _ = province_options(path)
    stored = st.session_state.get("selected_province")
    if stored in keys:
        return str(stored)
    return str(keys[0]) if keys else ""


def format_local_timestamp(value: object) -> str:
    if value is None or pd.isna(value):
        return "Chưa xác định"
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Ho_Chi_Minh")
    else:
        timestamp = timestamp.tz_convert("Asia/Ho_Chi_Minh")
    return timestamp.strftime("%H:%M %d/%m/%Y")


def render_modeled_disclaimer() -> None:
    """Kept as the name the remaining pages import, now backed by the expander.

    The old implementation was an st.info banner repeated at the foot of several
    pages. Identical text on every visit stops being read, so the content moved
    into a single collapsible methodology block. Pages still calling this name get
    the new behaviour; migrating their call sites is tracked in the roadmap.
    """

    methodology_expander()
