"""Canonical geography registry for Vietnam's 2025 province-level units.

The province codes follow Decision 19/2025/QD-TTg, effective 1 July 2025.
Coordinates are representative administrative anchors used for regional model
queries.  They must never be presented as street-level monitoring locations.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from types import MappingProxyType
from typing import Final

from vn_air_quality_weather.cities import City

VIETNAM_TIMEZONE: Final = "Asia/Ho_Chi_Minh"


def validate_vietnam_coordinates(latitude: float, longitude: float) -> None:
    """Reject coordinates outside a conservative Vietnam service envelope."""

    if not 7.0 <= latitude <= 24.0 or not 102.0 <= longitude <= 116.0:
        raise ValueError("coordinates are outside the supported Vietnam envelope")


@dataclass(frozen=True, slots=True)
class Province:
    """One current province-level administrative unit and its model anchor."""

    code: str
    key: str
    display_name: str
    unit_type: str
    anchor_name: str
    latitude: float
    longitude: float
    timezone: str = VIETNAM_TIMEZONE

    def __post_init__(self) -> None:
        if len(self.code) != 2 or not self.code.isdigit():
            raise ValueError("province code must be a two-digit string")
        if self.unit_type not in {"province", "municipality"}:
            raise ValueError("unit_type must be province or municipality")
        validate_vietnam_coordinates(self.latitude, self.longitude)

    def as_city(self) -> City:
        """Adapt a province anchor to the legacy location interface."""

        return City(
            key=self.key,
            display_name=self.display_name,
            latitude=self.latitude,
            longitude=self.longitude,
            timezone=self.timezone,
        )


_PROVINCE_ROWS: Final = (
    ("01", "hanoi", "Hà Nội", "municipality", "Hà Nội", 21.0278, 105.8342),
    ("04", "cao_bang", "Cao Bằng", "province", "Cao Bằng", 22.6667, 106.2500),
    ("08", "tuyen_quang", "Tuyên Quang", "province", "Tuyên Quang", 21.8236, 105.2142),
    ("11", "dien_bien", "Điện Biên", "province", "Điện Biên Phủ", 21.3860, 103.0230),
    ("12", "lai_chau", "Lai Châu", "province", "Lai Châu", 22.3864, 103.4703),
    ("14", "son_la", "Sơn La", "province", "Sơn La", 21.3256, 103.9188),
    ("15", "lao_cai", "Lào Cai", "province", "Lào Cai", 22.4856, 103.9707),
    ("19", "thai_nguyen", "Thái Nguyên", "province", "Thái Nguyên", 21.5942, 105.8482),
    ("20", "lang_son", "Lạng Sơn", "province", "Lạng Sơn", 21.8537, 106.7610),
    ("22", "quang_ninh", "Quảng Ninh", "province", "Hạ Long", 20.9500, 107.0734),
    ("24", "bac_ninh", "Bắc Ninh", "province", "Bắc Ninh", 21.1861, 106.0763),
    ("25", "phu_tho", "Phú Thọ", "province", "Việt Trì", 21.3227, 105.4019),
    ("31", "hai_phong", "Hải Phòng", "municipality", "Hải Phòng", 20.8449, 106.6881),
    ("33", "hung_yen", "Hưng Yên", "province", "Hưng Yên", 20.6464, 106.0511),
    ("37", "ninh_binh", "Ninh Bình", "province", "Ninh Bình", 20.2506, 105.9745),
    ("38", "thanh_hoa", "Thanh Hóa", "province", "Thanh Hóa", 19.8067, 105.7852),
    ("40", "nghe_an", "Nghệ An", "province", "Vinh", 18.6796, 105.6813),
    ("42", "ha_tinh", "Hà Tĩnh", "province", "Hà Tĩnh", 18.3428, 105.9057),
    ("44", "quang_tri", "Quảng Trị", "province", "Đông Hà", 16.8163, 107.1003),
    ("46", "hue", "Huế", "municipality", "Huế", 16.4637, 107.5909),
    ("48", "da_nang", "Đà Nẵng", "municipality", "Đà Nẵng", 16.0544, 108.2022),
    ("51", "quang_ngai", "Quảng Ngãi", "province", "Quảng Ngãi", 15.1205, 108.7923),
    ("52", "gia_lai", "Gia Lai", "province", "Quy Nhơn", 13.7820, 109.2190),
    ("56", "khanh_hoa", "Khánh Hòa", "province", "Nha Trang", 12.2388, 109.1967),
    ("66", "dak_lak", "Đắk Lắk", "province", "Buôn Ma Thuột", 12.6662, 108.0382),
    ("68", "lam_dong", "Lâm Đồng", "province", "Đà Lạt", 11.9404, 108.4583),
    ("75", "dong_nai", "Đồng Nai", "province", "Biên Hòa", 10.9574, 106.8427),
    (
        "79",
        "ho_chi_minh",
        "Hồ Chí Minh",
        "municipality",
        "Thành phố Hồ Chí Minh",
        10.8231,
        106.6297,
    ),
    ("80", "tay_ninh", "Tây Ninh", "province", "Tây Ninh", 11.3352, 106.1099),
    ("82", "dong_thap", "Đồng Tháp", "province", "Mỹ Tho", 10.3600, 106.3600),
    ("86", "vinh_long", "Vĩnh Long", "province", "Vĩnh Long", 10.2537, 105.9722),
    ("91", "an_giang", "An Giang", "province", "Rạch Giá", 10.0125, 105.0809),
    ("92", "can_tho", "Cần Thơ", "municipality", "Cần Thơ", 10.0452, 105.7469),
    ("96", "ca_mau", "Cà Mau", "province", "Cà Mau", 9.1769, 105.1500),
)


PROVINCES: Final[Mapping[str, Province]] = MappingProxyType(
    {
        row[1]: Province(
            code=row[0],
            key=row[1],
            display_name=row[2],
            unit_type=row[3],
            anchor_name=row[4],
            latitude=row[5],
            longitude=row[6],
        )
        for row in _PROVINCE_ROWS
    }
)

CORE_LOCATION_KEYS: Final = ("hanoi", "ho_chi_minh", "da_nang")


def province_cities(keys: Iterable[str] | None = None) -> tuple[City, ...]:
    """Return province anchors through the legacy city-shaped interface."""

    selected_keys = tuple(keys) if keys is not None else tuple(PROVINCES)
    unknown = sorted(set(selected_keys) - set(PROVINCES))
    if unknown:
        raise ValueError(f"unknown province keys: {', '.join(unknown)}")
    return tuple(PROVINCES[key].as_city() for key in selected_keys)


def province_by_code(code: str) -> Province:
    """Return a province by its official two-digit code."""

    for province in PROVINCES.values():
        if province.code == code:
            return province
    raise KeyError(code)


def nearest_province(latitude: float, longitude: float) -> tuple[Province, float]:
    """Return the closest province anchor and great-circle distance in kilometres.

    This is a spatial reference only, not an official administrative assignment.
    """

    validate_vietnam_coordinates(latitude, longitude)
    province = min(
        PROVINCES.values(),
        key=lambda candidate: haversine_km(
            latitude,
            longitude,
            candidate.latitude,
            candidate.longitude,
        ),
    )
    return province, round(
        haversine_km(latitude, longitude, province.latitude, province.longitude),
        1,
    )


def haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Calculate great-circle distance between two WGS84 coordinate pairs."""

    earth_radius_km = 6371.0088
    latitude_a_rad = radians(latitude_a)
    latitude_b_rad = radians(latitude_b)
    delta_latitude = radians(latitude_b - latitude_a)
    delta_longitude = radians(longitude_b - longitude_a)
    haversine = sin(delta_latitude / 2) ** 2 + (
        cos(latitude_a_rad) * cos(latitude_b_rad) * sin(delta_longitude / 2) ** 2
    )
    return 2 * earth_radius_km * asin(sqrt(haversine))
