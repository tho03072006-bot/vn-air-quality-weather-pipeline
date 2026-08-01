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
    openaq_api_key: SecretStr = SecretStr("")

    aws_region: str = ""
    aws_s3_bucket: str = ""
    aws_profile: str = ""

    duckdb_path: Path = Path("data/warehouse/vn_air_quality_weather.duckdb")
    local_raw_root: Path = Path("data/raw")
    raw_backend: Literal["local", "s3"] = "local"
    openaq_radius_meters: int = 25_000
    airflow_image_name: str = "apache/airflow:3.3.0"

    def require_openaq_api_key(self) -> str:
        """Return the OpenAQ key or fail without exposing the secret."""

        value = self.openaq_api_key.get_secret_value().strip()
        if not value:
            raise ValueError("OPENAQ_API_KEY is required when OpenAQ ingestion is enabled")
        return value

    def require_s3(self) -> tuple[str, str]:
        """Return required S3 settings for the cloud raw-storage backend."""

        if not self.aws_region or not self.aws_s3_bucket:
            raise ValueError("AWS_REGION and AWS_S3_BUCKET are required for raw_backend=s3")
        return self.aws_region, self.aws_s3_bucket


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance per Python process."""

    return Settings()
