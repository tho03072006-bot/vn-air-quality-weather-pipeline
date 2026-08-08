"""Ephemeral modeled forecasts for user-selected Vietnam coordinates."""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from vn_air_quality_weather.cities import City
from vn_air_quality_weather.clients.open_meteo import (
    OpenMeteoClient,
    normalize_air_quality_forecast,
    normalize_weather_forecast,
)
from vn_air_quality_weather.geography import VIETNAM_TIMEZONE, validate_vietnam_coordinates


@dataclass(frozen=True, slots=True)
class CustomLocation:
    """A user-selected WGS84 point inside the supported Vietnam envelope."""

    display_name: str
    latitude: float
    longitude: float
    timezone: str = VIETNAM_TIMEZONE

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        validate_vietnam_coordinates(self.latitude, self.longitude)
        ZoneInfo(self.timezone)

    @property
    def key(self) -> str:
        return f"custom_{self.latitude:.5f}_{self.longitude:.5f}".replace("-", "m").replace(
            ".", "p"
        )

    def as_city(self) -> City:
        return City(
            key=self.key,
            display_name=self.display_name.strip(),
            latitude=self.latitude,
            longitude=self.longitude,
            timezone=self.timezone,
        )


@dataclass(frozen=True, slots=True)
class OnDemandForecastHourly:
    """One joined air-quality/weather forecast hour for a custom point."""

    location_key: str
    forecast_issued_at_utc: datetime
    valid_at_utc: datetime
    valid_at_local: datetime
    lead_hours: int
    requested_latitude: float
    requested_longitude: float
    air_grid_latitude: float
    air_grid_longitude: float
    weather_grid_latitude: float
    weather_grid_longitude: float
    pm25_ugm3: float | None
    pm10_ugm3: float | None
    no2_ugm3: float | None
    o3_ugm3: float | None
    so2_ugm3: float | None
    co_ugm3: float | None
    temperature_2m_c: float | None
    apparent_temperature_c: float | None
    relative_humidity_2m_pct: float | None
    precipitation_probability_pct: float | None
    precipitation_mm: float | None
    wind_speed_10m_kmh: float | None
    wind_direction_10m_deg: float | None
    uv_index: float | None
    outdoor_score: float
    confidence_level: str
    decision_label: str
    decision_explanation: str
    source_type: str = "modeled"
    coverage_tier: str = "MODELED_ONLY"
    air_quality_source: str = "open_meteo_cams"
    weather_source: str = "open_meteo_forecast"
    resolution_note: str = "Regional model grid; not street-level monitoring"


def fetch_on_demand_forecast(
    location: CustomLocation,
    *,
    forecast_hours: int = 72,
    fetched_at_utc: datetime | None = None,
    client: OpenMeteoClient | None = None,
) -> tuple[OnDemandForecastHourly, ...]:
    """Fetch, normalize and score a temporary forecast without warehouse writes."""

    if not 1 <= forecast_hours <= 168:
        raise ValueError("forecast_hours must be between 1 and 168")
    issued_at = fetched_at_utc or datetime.now(UTC)
    if issued_at.tzinfo is None:
        raise ValueError("fetched_at_utc must be timezone-aware")
    issued_at = issued_at.astimezone(UTC)

    owns_client = client is None
    active_client = client or OpenMeteoClient()
    try:
        city = location.as_city()
        air_payload = active_client.fetch_air_quality_forecast(city, forecast_hours)
        weather_payload = active_client.fetch_weather_forecast(city, forecast_hours)
    finally:
        if owns_client:
            active_client.close()

    air_rows = normalize_air_quality_forecast(location.key, "00", issued_at, air_payload)
    weather_rows = normalize_weather_forecast(location.key, "00", issued_at, weather_payload)
    weather_by_time = {row.valid_at_utc: row for row in weather_rows}
    local_timezone = ZoneInfo(location.timezone)

    joined: list[OnDemandForecastHourly] = []
    for air in air_rows:
        weather = weather_by_time.get(air.valid_at_utc)
        if weather is None:
            continue
        lead_hours = max(0, int((air.valid_at_utc - issued_at).total_seconds() // 3600))
        score, label, explanation = score_outdoor_conditions(
            pm25_ugm3=air.pm2_5,
            precipitation_probability_pct=weather.precipitation_probability,
            apparent_temperature_c=weather.apparent_temperature,
            temperature_2m_c=weather.temperature_2m,
            uv_index=weather.uv_index,
        )
        confidence = (
            "MEDIUM"
            if air.pm2_5 is not None and weather.temperature_2m is not None and lead_hours <= 24
            else "LOW"
        )
        joined.append(
            OnDemandForecastHourly(
                location_key=location.key,
                forecast_issued_at_utc=issued_at,
                valid_at_utc=air.valid_at_utc,
                valid_at_local=air.valid_at_utc.astimezone(local_timezone),
                lead_hours=lead_hours,
                requested_latitude=location.latitude,
                requested_longitude=location.longitude,
                air_grid_latitude=air.grid_latitude,
                air_grid_longitude=air.grid_longitude,
                weather_grid_latitude=weather.grid_latitude,
                weather_grid_longitude=weather.grid_longitude,
                pm25_ugm3=air.pm2_5,
                pm10_ugm3=air.pm10,
                no2_ugm3=air.nitrogen_dioxide,
                o3_ugm3=air.ozone,
                so2_ugm3=air.sulphur_dioxide,
                co_ugm3=air.carbon_monoxide,
                temperature_2m_c=weather.temperature_2m,
                apparent_temperature_c=weather.apparent_temperature,
                relative_humidity_2m_pct=weather.relative_humidity_2m,
                precipitation_probability_pct=weather.precipitation_probability,
                precipitation_mm=weather.precipitation,
                wind_speed_10m_kmh=weather.wind_speed_10m,
                wind_direction_10m_deg=weather.wind_direction_10m,
                uv_index=weather.uv_index,
                outdoor_score=score,
                confidence_level=confidence,
                decision_label=label,
                decision_explanation=explanation,
            )
        )
    return tuple(joined)


def score_outdoor_conditions(
    *,
    pm25_ugm3: float | None,
    precipitation_probability_pct: float | None,
    apparent_temperature_c: float | None,
    temperature_2m_c: float | None,
    uv_index: float | None,
) -> tuple[float, str, str]:
    """Mirror the explainable dbt outdoor-planning heuristic."""

    air_penalty = min(70.0, (pm25_ugm3 if pm25_ugm3 is not None else 50.0) * 1.35)
    rain_penalty = min(15.0, (precipitation_probability_pct or 0.0) * 0.15)
    temperature_penalty = 0.0
    if apparent_temperature_c is not None:
        if apparent_temperature_c > 36.0:
            temperature_penalty = min(15.0, (apparent_temperature_c - 36.0) * 3.0)
        elif apparent_temperature_c < 15.0:
            temperature_penalty = min(15.0, (15.0 - apparent_temperature_c) * 2.0)
    uv_penalty = max(0.0, (uv_index or 0.0) - 5.0) * 2.0
    raw_score = 100.0 - air_penalty - rain_penalty - temperature_penalty - uv_penalty
    score = round(max(0.0, raw_score), 1)
    if raw_score >= 70.0:
        label = "Phù hợp hơn"
    elif raw_score >= 45.0:
        label = "Cân nhắc"
    else:
        label = "Nên hạn chế"

    apparent = apparent_temperature_c if apparent_temperature_c is not None else temperature_2m_c
    explanation = (
        f"PM2.5 {pm25_ugm3 or 0.0:.1f} µg/m³; "
        f"mưa {precipitation_probability_pct or 0.0:.0f}%; "
        f"cảm nhận {apparent or 0.0:.1f} °C"
    )
    return score, label, explanation
