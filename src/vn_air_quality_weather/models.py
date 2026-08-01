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
