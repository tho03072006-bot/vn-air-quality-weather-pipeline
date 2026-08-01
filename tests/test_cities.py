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


def test_default_duckdb_path() -> None:
    settings = Settings(
        _env_file=None,
        openaq_api_key="unit-test-key",
    )

    assert settings.duckdb_path.as_posix() == ("data/warehouse/vn_air_quality_weather.duckdb")
