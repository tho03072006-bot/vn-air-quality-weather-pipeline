from datetime import UTC, datetime

import pytest

from vn_air_quality_weather.on_demand import (
    CustomLocation,
    fetch_on_demand_forecast,
    score_outdoor_conditions,
)

AIR_PAYLOAD = {
    "latitude": 16.1,
    "longitude": 108.2,
    "hourly": {
        "time": ["2026-07-27T00:00", "2026-07-27T01:00"],
        "pm2_5": [10.0, 12.0],
        "pm10": [20.0, 22.0],
        "nitrogen_dioxide": [4.0, 5.0],
        "ozone": [50.0, 52.0],
        "sulphur_dioxide": [2.0, 2.1],
        "carbon_monoxide": [180.0, 181.0],
    },
}

WEATHER_PAYLOAD = {
    "latitude": 16.05,
    "longitude": 108.21,
    "hourly": {
        "time": ["2026-07-27T00:00", "2026-07-27T01:00"],
        "temperature_2m": [30.0, 31.0],
        "apparent_temperature": [32.0, 38.0],
        "relative_humidity_2m": [70.0, 72.0],
        "precipitation_probability": [10.0, 100.0],
        "precipitation": [0.0, 1.0],
        "wind_speed_10m": [8.0, 9.0],
        "wind_direction_10m": [180.0, 190.0],
        "uv_index": [0.0, 8.0],
    },
}


class FakeOpenMeteoClient:
    def fetch_air_quality_forecast(self, _location, forecast_hours: int):
        assert forecast_hours == 48
        return AIR_PAYLOAD

    def fetch_weather_forecast(self, _location, forecast_hours: int):
        assert forecast_hours == 48
        return WEATHER_PAYLOAD


def test_on_demand_forecast_joins_and_scores_matching_hours() -> None:
    location = CustomLocation("Hội An", 15.87944, 108.335)
    issued_at = datetime(2026, 7, 27, tzinfo=UTC)
    records = fetch_on_demand_forecast(
        location,
        forecast_hours=48,
        fetched_at_utc=issued_at,
        client=FakeOpenMeteoClient(),
    )

    assert len(records) == 2
    assert records[0].valid_at_local.hour == 7
    assert records[0].outdoor_score == 85.0
    assert records[0].decision_label == "Phù hợp hơn"
    assert records[0].confidence_level == "MEDIUM"
    assert records[0].coverage_tier == "MODELED_ONLY"
    assert records[0].requested_latitude == location.latitude
    assert records[0].air_grid_latitude == 16.1
    assert records[0].weather_grid_longitude == 108.21
    assert records[1].outdoor_score == 56.8


def test_outdoor_score_handles_missing_data_and_extremes() -> None:
    score, label, explanation = score_outdoor_conditions(
        pm25_ugm3=None,
        precipitation_probability_pct=None,
        apparent_temperature_c=None,
        temperature_2m_c=None,
        uv_index=None,
    )
    assert score == 32.5
    assert label == "Nên hạn chế"
    assert "PM2.5 0.0" in explanation

    score, label, _ = score_outdoor_conditions(
        pm25_ugm3=10.0,
        precipitation_probability_pct=100.0,
        apparent_temperature_c=38.0,
        temperature_2m_c=35.0,
        uv_index=8.0,
    )
    assert score == 59.5
    assert label == "Cân nhắc"


def test_custom_location_and_forecast_arguments_are_validated() -> None:
    with pytest.raises(ValueError, match="outside"):
        CustomLocation("Outside", 0.0, 0.0)
    with pytest.raises(ValueError, match="display_name"):
        CustomLocation(" ", 16.0, 108.0)
    with pytest.raises(ValueError, match="between 1 and 168"):
        fetch_on_demand_forecast(
            CustomLocation("Đà Nẵng", 16.0544, 108.2022),
            forecast_hours=0,
            client=FakeOpenMeteoClient(),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        fetch_on_demand_forecast(
            CustomLocation("Đà Nẵng", 16.0544, 108.2022),
            forecast_hours=48,
            fetched_at_utc=datetime(2026, 7, 27),
            client=FakeOpenMeteoClient(),
        )
