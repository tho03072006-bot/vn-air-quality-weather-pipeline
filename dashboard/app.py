import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.data_access import load_coverage, load_filter_options, load_hourly_mart

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = Path(
    os.environ.get(
        "DUCKDB_PATH",
        PROJECT_ROOT / "data" / "warehouse" / "vn_air_quality_weather.duckdb",
    )
)
CITY_LABELS = {
    "hanoi": "Hanoi",
    "ho_chi_minh": "Ho Chi Minh City",
    "da_nang": "Da Nang",
}
POLLUTANT_LABELS = {"pm25": "PM2.5", "pm10": "PM10", "no2": "NO₂", "o3": "O₃"}

st.set_page_config(
    page_title="Vietnam air quality and weather",
    page_icon=":material/air:",
    layout="wide",
)

st.title("Vietnam air quality and weather")
st.caption(
    "Hourly comparison for Hanoi, Ho Chi Minh City and Da Nang. "
    "Observed OpenAQ measurements and modeled CAMS estimates are never combined."
)


@st.cache_data(ttl="5m", max_entries=4)
def cached_options(database_path: str) -> dict[str, object]:
    return load_filter_options(Path(database_path))


@st.cache_data(ttl="5m", max_entries=32)
def cached_hourly(
    database_path: str,
    start_date,
    end_date,
    cities: tuple[str, ...],
    pollutant: str,
    source_type: str,
) -> pd.DataFrame:
    return load_hourly_mart(
        Path(database_path), start_date, end_date, cities, pollutant, source_type
    )


@st.cache_data(ttl="5m", max_entries=32)
def cached_coverage(
    database_path: str,
    start_date,
    end_date,
    cities: tuple[str, ...],
    pollutant: str,
    source_type: str,
) -> pd.DataFrame:
    return load_coverage(Path(database_path), start_date, end_date, cities, pollutant, source_type)


if not DATABASE_PATH.exists():
    st.error(
        f"Warehouse not found at `{DATABASE_PATH}`. Run the pipeline and `dbt build` first.",
        icon=":material/database_off:",
    )
    st.stop()

try:
    options = cached_options(str(DATABASE_PATH))
except Exception as error:
    st.error(
        "The analytics marts are unavailable. Run `dbt build` and check the dbt logs.",
        icon=":material/error:",
    )
    st.exception(error)
    st.stop()

with st.sidebar:
    st.header("Filters")
    date_range = st.date_input(
        "UTC date range",
        value=(options["min_date"], options["max_date"]),
        min_value=options["min_date"],
        max_value=options["max_date"],
    )
    selected_cities = st.multiselect(
        "Cities",
        options["cities"],
        default=options["cities"],
        format_func=lambda value: CITY_LABELS.get(value, value),
    )
    selected_pollutant = st.selectbox(
        "Pollutant",
        options["pollutants"],
        index=(options["pollutants"].index("pm25") if "pm25" in options["pollutants"] else 0),
        format_func=lambda value: POLLUTANT_LABELS.get(value, value.upper()),
    )
    selected_source_type = st.segmented_control(
        "Data type",
        options["source_types"],
        default=(
            "observed" if "observed" in options["source_types"] else options["source_types"][0]
        ),
        format_func=lambda value: "OpenAQ observed" if value == "observed" else "CAMS modeled",
    )

if not isinstance(date_range, tuple) or len(date_range) != 2:
    st.info("Select both a start date and an end date.")
    st.stop()
if not selected_cities or selected_source_type is None:
    st.info("Select at least one city and one data type.")
    st.stop()

start_date, end_date = date_range
try:
    data = cached_hourly(
        str(DATABASE_PATH),
        start_date,
        end_date,
        tuple(selected_cities),
        selected_pollutant,
        selected_source_type,
    )
    coverage = cached_coverage(
        str(DATABASE_PATH),
        start_date,
        end_date,
        tuple(selected_cities),
        selected_pollutant,
        selected_source_type,
    )
except Exception as error:
    st.error("The filtered mart query failed.", icon=":material/error:")
    st.exception(error)
    st.stop()

if data.empty:
    st.warning(
        "No rows match these filters. OpenAQ coverage can be absent for a city or pollutant; "
        "try CAMS modeled data for spatially complete coverage.",
        icon=":material/search_off:",
    )
    st.stop()

latest_timestamp = pd.to_datetime(data["observed_at_utc"], utc=True).max()
st.caption(f"Latest filtered observation: {latest_timestamp:%d %b %Y, %H:%M UTC}")

average_value = data["concentration"].mean()
maximum_value = data["concentration"].max()
hours_with_data = data["observed_at_utc"].nunique()
coverage_value = coverage["coverage_ratio"].mean() if not coverage.empty else 0.0

with st.container(horizontal=True):
    st.metric("Average concentration", f"{average_value:.1f} µg/m³", border=True)
    st.metric("Maximum concentration", f"{maximum_value:.1f} µg/m³", border=True)
    st.metric("Hours with data", f"{hours_with_data:,}", border=True)
    st.metric("Average coverage", f"{coverage_value:.0%}", border=True)
    st.metric("Average temperature", f"{data['temperature_2m_c'].mean():.1f} °C", border=True)
    st.metric("Average humidity", f"{data['relative_humidity_2m_pct'].mean():.0f}%", border=True)
    st.metric("Average wind speed", f"{data['wind_speed_10m_kmh'].mean():.1f} km/h", border=True)

trend_data = data.assign(city=data["city_key"].map(CITY_LABELS))
with st.container(border=True):
    st.subheader(f"{POLLUTANT_LABELS.get(selected_pollutant, selected_pollutant)} over time")
    st.line_chart(
        trend_data,
        x="observed_at_utc",
        y="concentration",
        color="city",
        x_label="UTC time",
        y_label="Concentration (µg/m³)",
    )

left, right = st.columns(2)
with left:
    with st.container(border=True):
        st.subheader("City comparison")
        city_summary = trend_data.groupby("city", as_index=False)["concentration"].mean()
        st.bar_chart(
            city_summary,
            x="city",
            y="concentration",
            x_label="City",
            y_label="Average concentration (µg/m³)",
        )
with right:
    with st.container(border=True):
        st.subheader("Hourly pattern")
        heatmap_data = trend_data.assign(
            hour=pd.to_datetime(trend_data["observed_at_utc"], utc=True).dt.hour
        )
        heatmap = (
            alt.Chart(heatmap_data)
            .mark_rect()
            .encode(
                x=alt.X("hour:O", title="Hour (UTC)"),
                y=alt.Y("city:N", title="City"),
                color=alt.Color("mean(concentration):Q", title="µg/m³"),
                tooltip=["city:N", "hour:O", alt.Tooltip("mean(concentration):Q", format=".1f")],
            )
        )
        st.altair_chart(heatmap)

left, right = st.columns(2)
with left:
    with st.container(border=True):
        st.subheader("Concentration and humidity")
        st.scatter_chart(
            trend_data,
            x="relative_humidity_2m_pct",
            y="concentration",
            color="city",
            x_label="Relative humidity (%)",
            y_label="Concentration (µg/m³)",
        )
with right:
    with st.container(border=True):
        st.subheader("Concentration and wind speed")
        st.scatter_chart(
            trend_data,
            x="wind_speed_10m_kmh",
            y="concentration",
            color="city",
            x_label="Wind speed (km/h)",
            y_label="Concentration (µg/m³)",
        )

with st.container(border=True):
    st.subheader("Rainy and dry hours")
    rain_summary = (
        trend_data.assign(
            weather=trend_data["precipitation_mm"]
            .fillna(0)
            .gt(0)
            .map({True: "Rainy", False: "Dry"})
        )
        .groupby(["city", "weather"], as_index=False)["concentration"]
        .mean()
    )
    st.bar_chart(
        rain_summary,
        x="city",
        y="concentration",
        color="weather",
        x_label="City",
        y_label="Average concentration (µg/m³)",
    )

with st.container(border=True):
    st.subheader("Coverage and missing hours")
    st.dataframe(
        coverage,
        hide_index=True,
        column_config={
            "coverage_ratio": st.column_config.ProgressColumn(
                "Coverage", min_value=0.0, max_value=1.0, format="percent"
            ),
            "data_date_utc": st.column_config.DateColumn("UTC date", format="DD MMM YYYY"),
            "hours_with_data": st.column_config.NumberColumn("Hours with data"),
            "expected_hours": st.column_config.NumberColumn("Expected hours"),
            "missing_hours": st.column_config.NumberColumn("Missing hours"),
        },
    )

st.info(
    "Correlation does not establish causation. This learning dashboard is not medical or "
    "public-health advice. OpenAQ coverage depends on reporting stations; CAMS values are "
    "modeled estimates, not station observations.",
    icon=":material/info:",
)
