from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ModeledAirQualityHourly:
    city_key: str
    observed_at_utc: datetime
    pm2_5: float | None
    pm10: float | None
    nitrogen_dioxide: float | None
    ozone: float | None
    grid_latitude: float
    grid_longitude: float
    source_name: str = "open_meteo_cams"
    source_type: str = "modeled"


@dataclass(frozen=True, slots=True)
class WeatherHourly:
    city_key: str
    observed_at_utc: datetime
    temperature_2m: float | None
    relative_humidity_2m: float | None
    precipitation: float | None
    wind_speed_10m: float | None
    wind_direction_10m: float | None
    grid_latitude: float
    grid_longitude: float
    source_name: str = "open_meteo_historical_forecast"


@dataclass(frozen=True, slots=True)
class ObservedAirQualityHourly:
    city_key: str
    station_id: str
    station_name: str
    sensor_id: int
    pollutant: str
    unit: str
    observed_at_utc: datetime
    value: float
    flagged: bool
    source_name: str = "openaq"
    source_type: str = "observed"
