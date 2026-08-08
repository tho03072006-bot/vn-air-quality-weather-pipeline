import pytest

from vn_air_quality_weather.settings import Settings


def test_settings_accepts_explicit_api_key() -> None:
    settings = Settings(
        _env_file=None,
        project_env="test",
        openaq_api_key="unit-test-key",
    )

    assert settings.project_env == "test"
    assert settings.openaq_api_key.get_secret_value() == "unit-test-key"
    assert "unit-test-key" not in repr(settings.openaq_api_key)


def test_default_duckdb_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # _env_file=None only stops the .env file being read; pydantic-settings still
    # honours the process environment. The README tells the reader to export
    # DUCKDB_PATH before launching the dashboard, so without clearing it here this
    # test failed for anyone who ran pytest in that same shell.
    monkeypatch.delenv("DUCKDB_PATH", raising=False)
    settings = Settings(
        _env_file=None,
        openaq_api_key="unit-test-key",
    )

    assert settings.duckdb_path.as_posix() == ("data/warehouse/vn_air_quality_weather.duckdb")


def test_retry_policy_reflects_configuration() -> None:
    settings = Settings(
        _env_file=None,
        openaq_api_key="unit-test-key",
        http_max_attempts=3,
        http_backoff_base_seconds=0.25,
        http_backoff_max_seconds=8.0,
    )

    policy = settings.retry_policy()

    assert policy.max_attempts == 3
    assert policy.backoff_base_seconds == 0.25
    assert policy.backoff_max_seconds == 8.0


def test_retry_policy_rejects_impossible_configuration() -> None:
    settings = Settings(
        _env_file=None,
        openaq_api_key="unit-test-key",
        http_max_attempts=0,
    )

    with pytest.raises(ValueError, match="max_attempts"):
        settings.retry_policy()
