from dataclasses import dataclass
from datetime import date, datetime


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
    # The station's own position, carried on every row rather than kept in a separate
    # registry. Denormalised on purpose: these rows are the only place a station's
    # coordinates reach the warehouse, and a dimension built from them stays correct
    # even when a station moves, because the rows before and after the move each carry
    # where they were measured.
    #
    # Nullable because OpenAQ can return a location without coordinates. A null here
    # means the model cannot be sampled at that station, which the decomposition
    # reports as an absent comparison rather than as a zero.
    station_latitude: float | None = None
    station_longitude: float | None = None
    source_name: str = "openaq"
    source_type: str = "observed"


@dataclass(frozen=True, slots=True)
class StationModeledAirQualityHourly:
    """The model sampled at a monitoring station's own coordinates.

    Deliberately its own table rather than another source_type inside
    air_quality_hourly. That table's source_type is grouped and partitioned by every
    model downstream of it and filtered by none, so a third value would quietly become
    a third series in the city mart, a third option in the History page's source
    picker, and a third input to the VN_AQI models. The project's rule is that
    observations and model output never become one series; this is model output at a
    station's coordinates, which is the easiest thing in the warehouse to mistake for
    an observation.

    It exists to split the half of finding O that forecast drift did not explain.
    Against the anchor series it gives representativeness -- pure distance, no
    observation needed, so it is measurable even where no station reports. Against the
    station's own readings it gives the model's offset where the instrument actually
    stands.
    """

    station_id: str
    city_key: str
    observed_at_utc: datetime
    pm2_5: float | None
    pm10: float | None
    nitrogen_dioxide: float | None
    ozone: float | None
    station_latitude: float
    station_longitude: float
    grid_latitude: float
    grid_longitude: float
    source_name: str = "open_meteo_cams"
    source_type: str = "modeled_at_station"


@dataclass(frozen=True, slots=True)
class PipelineRunAudit:
    """One row per pipeline execution, used to prove freshness and lineage.

    The fields below `pipeline_version` all carry defaults so the historical
    path keeps its existing call shape, while the forecast path can record an
    outcome that is neither total success nor total failure.
    """

    run_id: str
    data_date: date
    started_at_utc: datetime
    finished_at_utc: datetime
    duration_seconds: float
    raw_backend: str
    include_openaq: bool
    # Objects the run attempted to write. The created/reused split below says how
    # many of those were genuinely new.
    raw_objects: int
    weather_rows: int
    observed_air_quality_rows: int
    modeled_air_quality_rows: int
    pipeline_version: str = "0.1.0"
    pipeline_name: str = "historical"
    # PARTIAL exists because throwing away thirty-three provinces that landed
    # because one timed out discards work the provider has already been charged
    # for, and leaves the warehouse looking as if nothing ran.
    status: str = "SUCCESS"
    requested_location_count: int = 0
    succeeded_location_count: int = 0
    failed_location_count: int = 0
    # RawJsonStore.write already reports whether it wrote a new object, so
    # counting every call as created overstates what the run produced.
    raw_objects_created: int = 0
    raw_objects_reused: int = 0
    weather_forecast_rows: int = 0
    air_quality_forecast_rows: int = 0
    # Never populate these from a raw exception string: request URLs can carry an
    # API key. Use the redacting helper in the forecast pipeline.
    error_category: str = ""
    error_summary: str = ""


@dataclass(frozen=True, slots=True)
class AirQualityForecastHourly:
    """One modeled air-quality forecast vintage for a province anchor."""

    location_key: str
    province_code: str
    forecast_issued_at_utc: datetime
    valid_at_utc: datetime
    pm2_5: float | None
    pm10: float | None
    nitrogen_dioxide: float | None
    ozone: float | None
    sulphur_dioxide: float | None
    carbon_monoxide: float | None
    grid_latitude: float
    grid_longitude: float
    source_name: str = "open_meteo_cams"
    source_type: str = "modeled"
    resolution_note: str = "Regional model grid; not street-level monitoring"


@dataclass(frozen=True, slots=True)
class WeatherForecastHourly:
    """One weather forecast vintage for a province anchor."""

    location_key: str
    province_code: str
    forecast_issued_at_utc: datetime
    valid_at_utc: datetime
    temperature_2m: float | None
    apparent_temperature: float | None
    relative_humidity_2m: float | None
    precipitation_probability: float | None
    precipitation: float | None
    wind_speed_10m: float | None
    wind_direction_10m: float | None
    uv_index: float | None
    grid_latitude: float
    grid_longitude: float
    source_name: str = "open_meteo_forecast"
