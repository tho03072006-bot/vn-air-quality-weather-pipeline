from datetime import date
from pathlib import Path

import duckdb
import pandas as pd


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
            data_date_utc,
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
        where data_date_utc between ? and ?
          and city_key in ({city_placeholders})
          and source_type = ?
          and is_publishable
          and aqi_daily is not null
        order by data_date_utc desc, city_key
    """
    parameters = [start_date, end_date, *cities, source_type]
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, parameters).fetchdf()


def load_pipeline_runs(database_path: Path, limit: int = 20) -> pd.DataFrame:
    """Return the most recent pipeline executions for the freshness panel."""

    query = """
        select
            run_id,
            data_date_utc,
            started_at_utc,
            finished_at_utc,
            duration_seconds,
            raw_backend,
            raw_objects,
            weather_rows,
            observed_air_quality_rows,
            modeled_air_quality_rows,
            total_rows,
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
