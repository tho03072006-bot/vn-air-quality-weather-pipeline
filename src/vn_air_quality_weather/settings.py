from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from vn_air_quality_weather.retry import RetryPolicy

# Every Airflow task that writes the warehouse or runs dbt declares this pool, and
# the pool is created with a single slot. DuckDB takes one writer at a time, and
# max_active_runs only bounds a single DAG, so without this the historical and
# forecast DAGs can each be inside dbt build on the same file. Defined here rather
# than in a DAG file so the DAG structure test can assert against one constant.
WAREHOUSE_WRITER_POOL = "warehouse_writer"


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

    http_timeout_seconds: float = 30.0
    http_max_attempts: int = 5
    http_backoff_base_seconds: float = 0.5
    http_backoff_max_seconds: float = 30.0

    # A national forecast run issues two requests per province. Kept deliberately
    # low: Open-Meteo's free tier is rate limited, and a burst that trips the
    # limit turns a healthy run into a partial one.
    forecast_max_workers: int = 4

    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: SecretStr = SecretStr("")
    alert_cooldown_minutes: int = 180
    alert_max_data_age_minutes: int = 180

    # The serving database. DuckDB stays the analytical warehouse that dbt builds
    # into; SQL Server holds only the marts the web application reads, republished
    # in full after every pipeline run. The split exists because DuckDB admits one
    # writer or many readers and not both: a web process holding a read connection
    # would take the file lock the 02:00 DAG needs, and the failure would surface as
    # a broken pipeline rather than a broken page.
    sqlserver_host: str = "localhost"
    sqlserver_port: int = 1433
    sqlserver_database: str = "vn_air_quality"
    sqlserver_user: str = ""
    sqlserver_password: SecretStr = SecretStr("")
    sqlserver_driver: str = "ODBC Driver 18 for SQL Server"
    # Driver 18 encrypts by default and then verifies the certificate, which a local
    # developer instance signs itself. Trusting it is correct for localhost and wrong
    # for anything else, so both stay configurable rather than hard-coded.
    sqlserver_encrypt: bool = True
    sqlserver_trust_server_certificate: bool = True

    def retry_policy(self) -> RetryPolicy:
        """Build the shared HTTP retry policy from configuration."""

        return RetryPolicy(
            max_attempts=self.http_max_attempts,
            backoff_base_seconds=self.http_backoff_base_seconds,
            backoff_max_seconds=self.http_backoff_max_seconds,
        )

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

    def require_sqlserver_odbc(self) -> str:
        """Return the ODBC connection string, or fail without exposing the secret.

        Built here rather than in the publishing script so that the script, the test
        suite and any future reader agree on one spelling of the connection. The
        password is read from SecretStr at the last moment and never returned on its
        own: the only value that leaves this method is the assembled string, which
        callers pass straight to the driver.

        ODBC delimits a value with braces, so a password containing a brace has to
        double it. That is not hypothetical -- the password here is meant to be
        replaced with a generated one, and generated passwords contain punctuation.
        """

        if not self.sqlserver_user:
            raise ValueError("SQLSERVER_USER is required to publish to SQL Server")
        secret = self.sqlserver_password.get_secret_value()
        if not secret:
            raise ValueError("SQLSERVER_PASSWORD is required to publish to SQL Server")

        encrypt = "yes" if self.sqlserver_encrypt else "no"
        trust = "yes" if self.sqlserver_trust_server_certificate else "no"
        return ";".join(
            (
                f"DRIVER={{{self.sqlserver_driver}}}",
                f"SERVER=tcp:{self.sqlserver_host},{self.sqlserver_port}",
                f"DATABASE={self.sqlserver_database}",
                f"UID={{{self.sqlserver_user.replace('}', '}}')}}}",
                f"PWD={{{secret.replace('}', '}}')}}}",
                f"Encrypt={encrypt}",
                f"TrustServerCertificate={trust}",
            )
        )


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance per Python process."""

    return Settings()
