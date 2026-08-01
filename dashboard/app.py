import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

try:
    from dashboard.data_access import (
        load_aqi_daily,
        load_aqi_hourly,
        load_coverage,
        load_filter_options,
        load_hourly_mart,
        load_pipeline_runs,
    )
except ModuleNotFoundError as error:
    if error.name != "dashboard":
        raise
    from data_access import (
        load_aqi_daily,
        load_aqi_hourly,
        load_coverage,
        load_filter_options,
        load_hourly_mart,
        load_pipeline_runs,
    )

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


@st.cache_data(ttl="5m", max_entries=32)
def cached_aqi_hourly(
    database_path: str,
    start_date,
    end_date,
    cities: tuple[str, ...],
    source_type: str,
) -> pd.DataFrame:
    return load_aqi_hourly(Path(database_path), start_date, end_date, cities, source_type)


@st.cache_data(ttl="5m", max_entries=32)
def cached_aqi_daily(
    database_path: str,
    start_date,
    end_date,
    cities: tuple[str, ...],
    source_type: str,
) -> pd.DataFrame:
    return load_aqi_daily(Path(database_path), start_date, end_date, cities, source_type)


@st.cache_data(ttl="5m", max_entries=4)
def cached_pipeline_runs(database_path: str) -> pd.DataFrame:
    return load_pipeline_runs(Path(database_path))


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

tab_pollution, tab_aqi, tab_pipeline = st.tabs(
    ["Concentration and weather", "VN_AQI", "Pipeline health"]
)

with tab_pollution:
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
                    tooltip=[
                        "city:N",
                        "hour:O",
                        alt.Tooltip("mean(concentration):Q", format=".1f"),
                    ],
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

with tab_aqi:
    st.caption(
        "VN_AQI follows Quyet dinh 1459/QD-TCMT (12/11/2019): PM2.5 and PM10 use the "
        "Nowcast weighted mean for the hourly index and the 24-hour mean for the daily "
        "index, and the published value is the highest pollutant sub-index. Hours "
        "without a particulate reading are excluded because the decision requires "
        "PM10 or PM2.5."
    )
    try:
        aqi_hourly = cached_aqi_hourly(
            str(DATABASE_PATH),
            start_date,
            end_date,
            tuple(selected_cities),
            selected_source_type,
        )
        aqi_daily = cached_aqi_daily(
            str(DATABASE_PATH),
            start_date,
            end_date,
            tuple(selected_cities),
            selected_source_type,
        )
    except Exception as error:
        st.error("The VN_AQI marts could not be queried.", icon=":material/error:")
        st.exception(error)
        aqi_hourly = pd.DataFrame()
        aqi_daily = pd.DataFrame()

    if aqi_hourly.empty:
        st.info(
            "No publishable VN_AQI hours for this filter. The index needs PM2.5 or PM10, "
            "so try the CAMS modeled source or a wider date range.",
            icon=":material/info:",
        )
    else:
        aqi_trend = aqi_hourly.assign(city=aqi_hourly["city_key"].map(CITY_LABELS))
        worst = aqi_trend.loc[aqi_trend["aqi_hourly"].idxmax()]

        with st.container(horizontal=True):
            st.metric("Average AQI (hourly)", f"{aqi_trend['aqi_hourly'].mean():.0f}", border=True)
            st.metric(
                "Peak AQI",
                f"{int(worst['aqi_hourly'])}",
                delta=f"{worst['city']} · {worst['category_vi']}",
                delta_color="off",
                border=True,
            )
            st.metric(
                "Most frequent dominant pollutant",
                POLLUTANT_LABELS.get(
                    aqi_trend["dominant_pollutant"].mode().iloc[0],
                    aqi_trend["dominant_pollutant"].mode().iloc[0],
                ),
                border=True,
            )
            st.metric("Publishable hours", f"{len(aqi_trend):,}", border=True)

        with st.container(border=True):
            st.subheader("VN_AQI gio over time")
            st.line_chart(
                aqi_trend,
                x="observed_at_utc",
                y="aqi_hourly",
                color="city",
                x_label="UTC time",
                y_label="VN_AQI",
            )

        left, right = st.columns(2)
        with left:
            with st.container(border=True):
                st.subheader("Hours in each VN_AQI band")
                band_counts = (
                    aqi_trend.groupby(["city", "category_vi"], as_index=False)
                    .size()
                    .rename(columns={"size": "hours"})
                )
                st.bar_chart(
                    band_counts,
                    x="city",
                    y="hours",
                    color="category_vi",
                    x_label="City",
                    y_label="Hours",
                )
        with right:
            with st.container(border=True):
                st.subheader("Dominant pollutant share")
                dominant = (
                    aqi_trend.groupby(["city", "dominant_pollutant"], as_index=False)
                    .size()
                    .rename(columns={"size": "hours"})
                )
                st.bar_chart(
                    dominant,
                    x="city",
                    y="hours",
                    color="dominant_pollutant",
                    x_label="City",
                    y_label="Hours",
                )

    if not aqi_daily.empty:
        with st.container(border=True):
            st.subheader("VN_AQI ngay")
            daily_view = aqi_daily.assign(city=aqi_daily["city_key"].map(CITY_LABELS))
            st.dataframe(
                daily_view[
                    [
                        "data_date_utc",
                        "city",
                        "aqi_daily",
                        "category_vi",
                        "dominant_pollutant",
                        "pm25_mean_24h",
                        "pm10_mean_24h",
                        "no2_max_1h",
                        "o3_max_1h",
                        "o3_max_8h",
                        "pm25_hours",
                        "advice_sensitive_vi",
                    ]
                ],
                hide_index=True,
                column_config={
                    "data_date_utc": st.column_config.DateColumn("UTC date", format="DD MMM YYYY"),
                    "city": st.column_config.TextColumn("City"),
                    "aqi_daily": st.column_config.NumberColumn("VN_AQI ngay"),
                    "category_vi": st.column_config.TextColumn("Muc"),
                    "dominant_pollutant": st.column_config.TextColumn("Thong so quyet dinh"),
                    "pm25_mean_24h": st.column_config.NumberColumn("PM2.5 TB24h", format="%.1f"),
                    "pm10_mean_24h": st.column_config.NumberColumn("PM10 TB24h", format="%.1f"),
                    "no2_max_1h": st.column_config.NumberColumn("NO₂ max 1h", format="%.1f"),
                    "o3_max_1h": st.column_config.NumberColumn("O₃ max 1h", format="%.1f"),
                    "o3_max_8h": st.column_config.NumberColumn("O₃ max 8h", format="%.1f"),
                    "pm25_hours": st.column_config.NumberColumn("Gio PM2.5"),
                    "advice_sensitive_vi": st.column_config.TextColumn("Khuyen nghi nhom nhay cam"),
                },
            )

with tab_pipeline:
    st.caption(
        "Every pipeline execution writes an audit row, so freshness and row counts come "
        "from the warehouse rather than from log files."
    )
    try:
        runs = cached_pipeline_runs(str(DATABASE_PATH))
    except Exception as error:
        st.error("The pipeline audit table could not be queried.", icon=":material/error:")
        st.exception(error)
        runs = pd.DataFrame()

    if runs.empty:
        st.info("No pipeline runs recorded yet.", icon=":material/info:")
    else:
        last_finished = pd.to_datetime(runs["finished_at_utc"], utc=True).max()
        last_run_rows = pd.to_numeric(runs["total_rows"], errors="coerce").iloc[0]
        median_duration = pd.to_numeric(runs["duration_seconds"], errors="coerce").median()
        with st.container(horizontal=True):
            st.metric("Recorded runs", f"{len(runs):,}", border=True)
            st.metric("Last finished", f"{last_finished:%d %b %Y, %H:%M UTC}", border=True)
            st.metric(
                "Median duration",
                "n/a" if pd.isna(median_duration) else f"{median_duration:.0f} s",
                border=True,
            )
            st.metric(
                "Rows in last run",
                "n/a" if pd.isna(last_run_rows) else f"{int(last_run_rows):,}",
                border=True,
            )

        with st.container(border=True):
            st.subheader("Recent runs")
            st.dataframe(
                runs,
                hide_index=True,
                column_config={
                    "data_date_utc": st.column_config.DateColumn(
                        "UTC data date", format="DD MMM YYYY"
                    ),
                    "duration_seconds": st.column_config.NumberColumn(
                        "Duration (s)", format="%.0f"
                    ),
                    "is_latest_run_for_date": st.column_config.CheckboxColumn("Latest for date"),
                },
            )

st.info(
    "Correlation does not establish causation. This learning dashboard is not medical or "
    "public-health advice. OpenAQ coverage depends on reporting stations; CAMS values are "
    "modeled estimates, not station observations. VN_AQI here is computed from these two "
    "sources and is not an official Bo Tai nguyen va Moi truong publication.",
    icon=":material/info:",
)
