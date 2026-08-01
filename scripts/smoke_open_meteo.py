import argparse
from datetime import date

from vn_air_quality_weather.cities import CITIES
from vn_air_quality_weather.clients.open_meteo import (
    OpenMeteoClient,
    normalize_modeled_air_quality,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test Open-Meteo modeled air-quality data.")
    parser.add_argument(
        "--date",
        required=True,
        type=date.fromisoformat,
        help="Closed data date in YYYY-MM-DD format.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with OpenMeteoClient() as client:
        for city in CITIES.values():
            payload = client.fetch_modeled_air_quality(
                city=city,
                data_date=args.date,
            )
            records = normalize_modeled_air_quality(
                city_key=city.key,
                payload=payload,
            )

            non_null_counts = {
                "pm25": sum(record.pm2_5 is not None for record in records),
                "pm10": sum(record.pm10 is not None for record in records),
                "no2": sum(record.nitrogen_dioxide is not None for record in records),
                "o3": sum(record.ozone is not None for record in records),
            }

            first_hour = records[0].observed_at_utc.isoformat() if records else "none"
            last_hour = records[-1].observed_at_utc.isoformat() if records else "none"

            print(
                f"{city.key}: hours={len(records)}, "
                f"non_null={non_null_counts}, "
                f"first={first_hour}, last={last_hour}"
            )

            if len(records) != 24:
                raise RuntimeError(f"{city.key}: expected 24 hours, got {len(records)}")


if __name__ == "__main__":
    main()
