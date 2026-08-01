from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_env: Literal["development", "test", "production"] = "development"
    openaq_api_key: SecretStr

    aws_region: str = ""
    aws_s3_bucket: str = ""
    aws_profile: str = ""

    duckdb_path: Path = Path("data/warehouse/vn_air_quality_weather.duckdb")
    airflow_image_name: str = "apache/airflow:3.3.0"


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance per Python process."""

    return Settings()
