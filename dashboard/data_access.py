from datetime import date
from pathlib import Path

import duckdb
import pandas as pd


def load_filter_options(database_path: Path) -> dict[str, object]:
    with duckdb.connect(str(database_path), read_only=True) as connection:
        row = connection.execute(
            """
            select
                min(cast(observed_at_utc as date)),
                max(cast(observed_at_utc as date)),
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
        where cast(observed_at_utc as date) between ? and ?
          and city_key in ({city_placeholders})
          and pollutant = ?
          and source_type = ?
        order by observed_at_utc, city_key
    """
    parameters = [start_date, end_date, *cities, pollutant, source_type]
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return connection.execute(query, parameters).fetchdf()


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
