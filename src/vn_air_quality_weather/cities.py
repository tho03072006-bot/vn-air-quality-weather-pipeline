from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class City:
    key: str
    display_name: str
    latitude: float
    longitude: float
    timezone: str = "Asia/Ho_Chi_Minh"


CITIES: Final[Mapping[str, City]] = MappingProxyType(
    {
        "hanoi": City(
            key="hanoi",
            display_name="Hanoi",
            latitude=21.0278,
            longitude=105.8342,
        ),
        "ho_chi_minh": City(
            key="ho_chi_minh",
            display_name="Ho Chi Minh City",
            latitude=10.8231,
            longitude=106.6297,
        ),
        "da_nang": City(
            key="da_nang",
            display_name="Da Nang",
            latitude=16.0544,
            longitude=108.2022,
        ),
    }
)
