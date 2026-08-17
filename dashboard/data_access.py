from datetime import date
from pathlib import Path

import duckdb
import pandas as pd


def relation_exists(database_path: Path, schema: str, relation: str) -> bool:
    """Return whether a serving relation exists without mutating the warehouse."""

    query = """
        select count(*) > 0
        from information_schema.tables
        where table_schema = ? and table_name = ?
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return bool(connection.execute(query, [schema, relation]).fetchone()[0])


def load_provinces(database_path: Path) -> pd.DataFrame:
    """Return the canonical 34-unit province registry."""

    query = """
        select
            province_code,
            province_key,
            province_name,
            unit_type,
            anchor_name,
            latitude,
            longitude,
            timezone_name
        from analytics.dim_province
        order by province_code
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query).fetchdf()


def load_current_conditions(database_path: Path, location_key: str | None = None) -> pd.DataFrame:
    """Return the latest modeled condition per province anchor."""

    predicate = "where location_key = ?" if location_key else ""
    parameters = [location_key] if location_key else []
    query = f"""
        select *
        from analytics.mart_current_conditions
        {predicate}
        order by province_code
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, parameters).fetchdf()


def load_location_forecast(
    database_path: Path,
    location_key: str,
    hours: int = 72,
) -> pd.DataFrame:
    """Return the latest modeled forecast horizon for one location."""

    query = """
        select *
        from analytics.mart_location_hourly_forecast
        where location_key = ?
          and valid_at_utc >= date_trunc('hour', current_timestamp) - interval '1 hour'
          and valid_at_utc < date_trunc('hour', current_timestamp) + (? * interval '1 hour')
        order by valid_at_utc
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, [location_key, hours]).fetchdf()


def load_decision_windows(
    database_path: Path,
    location_key: str,
    limit: int = 5,
) -> pd.DataFrame:
    """Return the strongest explainable outdoor windows for a location."""

    query = """
        select *
        from analytics.mart_outdoor_decision_window
        where location_key = ?
        order by suitability_rank
        limit ?
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, [location_key, limit]).fetchdf()


def load_contiguous_windows(
    database_path: Path,
    location_key: str,
    limit_per_duration: int = 5,
) -> pd.DataFrame:
    """Return the strongest contiguous two-hour and three-hour windows."""

    query = """
        select *
        from analytics.mart_outdoor_contiguous_window
        where location_key = ?
          and suitability_rank <= ?
        order by duration_hours, suitability_rank
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, [location_key, limit_per_duration]).fetchdf()


def load_pipeline_health(database_path: Path) -> pd.DataFrame:
    """Summarize row volume and freshness for each raw source table."""

    query = """
        select 'OpenAQ/CAMS history' as source, count(*) as row_count,
               max(observed_at_utc) as latest_fetch_utc
        from raw.air_quality_hourly
        union all
        select 'Weather history', count(*), max(observed_at_utc)
        from raw.weather_hourly
        union all
        select 'Air-quality forecast', count(*), max(forecast_issued_at_utc)
        from raw.air_quality_forecast_hourly
        union all
        select 'Weather forecast', count(*), max(forecast_issued_at_utc)
        from raw.weather_forecast_hourly
        order by source
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query).fetchdf()


def load_filter_options(database_path: Path) -> dict[str, object]:
    with duckdb.connect(str(database_path), read_only=True) as connection:
        row = connection.execute(
            """
            select
                min(cast(observed_at_utc at time zone 'UTC' as date)),
                max(cast(observed_at_utc at time zone 'UTC' as date)),
                list(distinct city_key order by city_key),
                list(distinct pollutant order by pollutant),
                list(distinct source_type order by source_type)
            from analytics.mart_city_air_quality_hourly
            """
        ).fetchone()
    if row is None or row[0] is None:
        raise ValueError("The analytics mart does not contain data")
    return {
        "min_date": row[0],
        "max_date": row[1],
        "cities": row[2],
        "pollutants": row[3],
        "source_types": row[4],
    }


def load_hourly_mart(
    database_path: Path,
    start_date: date,
    end_date: date,
    cities: tuple[str, ...],
    pollutant: str,
    source_type: str,
) -> pd.DataFrame:
    if not cities:
        return pd.DataFrame()
    city_placeholders = ", ".join("?" for _ in cities)
    query = f"""
        select *
        from analytics.mart_city_air_quality_hourly
        where cast(observed_at_utc at time zone 'UTC' as date) between ? and ?
          and city_key in ({city_placeholders})
          and pollutant = ?
          and source_type = ?
        order by observed_at_utc, city_key
    """
    parameters = [start_date, end_date, *cities, pollutant, source_type]
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, parameters).fetchdf()


def load_aqi_hourly(
    database_path: Path,
    start_date: date,
    end_date: date,
    cities: tuple[str, ...],
    source_type: str,
) -> pd.DataFrame:
    """Return VN_AQI gio rows, restricted to hours the decision allows publishing."""

    if not cities:
        return pd.DataFrame()
    city_placeholders = ", ".join("?" for _ in cities)
    query = f"""
        select
            city_key,
            observed_at_utc,
            observed_at_local,
            aqi_hourly,
            dominant_pollutant,
            aqi_pm25,
            aqi_pm10,
            aqi_no2,
            aqi_o3,
            category_vi,
            category_en,
            colour_hex,
            advice_general_vi,
            advice_sensitive_vi
        from analytics.mart_city_aqi_hourly
        where cast(observed_at_utc at time zone 'UTC' as date) between ? and ?
          and city_key in ({city_placeholders})
          and source_type = ?
          and is_publishable
          and aqi_hourly is not null
        order by observed_at_utc, city_key
    """
    parameters = [start_date, end_date, *cities, source_type]
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, parameters).fetchdf()


def load_aqi_daily(
    database_path: Path,
    start_date: date,
    end_date: date,
    cities: tuple[str, ...],
    source_type: str,
) -> pd.DataFrame:
    """Return VN_AQI ngay rows for the filtered slice."""

    if not cities:
        return pd.DataFrame()
    city_placeholders = ", ".join("?" for _ in cities)
    query = f"""
        select
            city_key,
            data_date_vn,
            aqi_daily,
            dominant_pollutant,
            aqi_pm25,
            aqi_pm10,
            aqi_no2,
            aqi_o3,
            pm25_mean_24h,
            pm10_mean_24h,
            no2_max_1h,
            o3_max_1h,
            o3_max_8h,
            pm25_hours,
            pm10_hours,
            category_vi,
            category_en,
            colour_hex,
            health_effect_vi,
            advice_general_vi,
            advice_sensitive_vi
        from analytics.mart_city_aqi_daily
        where data_date_vn between ? and ?
          and city_key in ({city_placeholders})
          and source_type = ?
          and is_publishable
          and aqi_daily is not null
        order by data_date_vn desc, city_key
    """
    parameters = [start_date, end_date, *cities, source_type]
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, parameters).fetchdf()


def load_pipeline_runs(database_path: Path, limit: int = 20) -> pd.DataFrame:
    """Return the most recent pipeline executions for the freshness panel."""

    query = """
        select
            run_id,
            pipeline_name,
            status,
            data_date_utc,
            started_at_utc,
            finished_at_utc,
            duration_seconds,
            raw_backend,
            raw_objects_attempted,
            raw_objects_created,
            raw_objects_reused,
            requested_location_count,
            succeeded_location_count,
            failed_location_count,
            weather_rows,
            observed_air_quality_rows,
            modeled_air_quality_rows,
            total_rows,
            total_forecast_rows,
            error_category,
            error_summary,
            is_latest_run_for_date
        from analytics.fct_pipeline_run
        order by finished_at_utc desc
        limit ?
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, [limit]).fetchdf()


def load_coverage(
    database_path: Path,
    start_date: date,
    end_date: date,
    cities: tuple[str, ...],
    pollutant: str,
    source_type: str,
) -> pd.DataFrame:
    if not cities:
        return pd.DataFrame()
    city_placeholders = ", ".join("?" for _ in cities)
    query = f"""
        select
            city_key,
            data_date_utc,
            pollutant,
            source_name,
            source_type,
            hours_with_data,
            expected_hours,
            coverage_ratio,
            missing_hours
        from analytics.mart_data_coverage
        where data_date_utc between ? and ?
          and city_key in ({city_placeholders})
          and pollutant = ?
          and source_type = ?
        order by data_date_utc desc, city_key
    """
    parameters = [start_date, end_date, *cities, pollutant, source_type]
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, parameters).fetchdf()


def load_forecast_vs_analysis(database_path: Path) -> pd.DataFrame:
    """Return forecast drift against the model's own analysis, by lead band.

    The companion to load_model_station_discrepancy and meaningless without it: this
    is the part of the published gap that is about forecasting, and that mart is the
    part that is not.
    """

    query = """
        select
            location_key,
            pollutant,
            lead_band,
            unit,
            paired_hours,
            pending_hours,
            has_sufficient_sample,
            min_paired_hours,
            mean_forecast_ugm3,
            mean_analysis_ugm3,
            mean_abs_drift_ugm3,
            mean_signed_drift_ugm3
        from analytics.mart_forecast_vs_analysis
        order by location_key, pollutant, lead_band
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query).fetchdf()


def load_model_station_discrepancy(database_path: Path) -> pd.DataFrame:
    """Return the model-versus-station gap by location, pollutant and lead band.

    Deliberately not named accuracy, here or anywhere downstream. The mart measures
    a CAMS grid cell against one street-level station, which is model error and
    representativeness error added together.
    """

    query = """
        select
            location_key,
            pollutant,
            lead_band,
            unit,
            paired_hours,
            pending_hours,
            unverifiable_hours,
            vintages,
            has_sufficient_sample,
            min_paired_hours,
            mean_forecast_ugm3,
            mean_observed_ugm3,
            mean_abs_discrepancy_ugm3,
            rms_discrepancy_ugm3,
            mean_signed_discrepancy_ugm3
        from analytics.mart_model_station_discrepancy
        order by location_key, pollutant, lead_band
    """
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query).fetchdf()
