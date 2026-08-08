# Sources, attribution and limitations

## Administrative geography

The 34 province-level codes and names in `provinces_2025.csv` follow the
[Government of Vietnam catalogue effective 1 July 2025](https://xaydungchinhsach.chinhphu.vn/bang-danh-muc-va-ma-so-cua-34-tinh-thanh-moi-cac-don-vi-hanh-chinh-cap-xa-moi-11925070418263625.htm).
The catalogue also has 3,321 commune-level units. This MVP precomputes only 34
province anchors. It now supports ephemeral place search and custom-coordinate
forecast caching, but has not imported the official commune registry or mapped
geocoder results to official commune/province codes.

## Place search and custom coordinates

The custom-location page uses the
[Open-Meteo Geocoding API](https://open-meteo.com/en/docs/geocoding-api), whose
location records are based on GeoNames. Requests are restricted to country code
`VN`; names and administrative strings remain search metadata rather than an
authoritative 2025 boundary assignment. User-entered coordinates are WGS84 and
must fall inside the project's conservative Vietnam service envelope.

Results are cached in the Streamlit process for 15 minutes and are not persisted
as saved places, warehouse dimensions or forecast vintages. The UI exposes the
requested coordinate and the model grid coordinate because Open-Meteo may select
a nearby grid-cell rather than the exact point.

## Air-quality observations

OpenAQ v3 provides station and sensor observations. Its `latest` resource is a
convenience view; reconciliation must use hourly measurements because records
can arrive later. The default free limit is 60 requests/minute and 2,000/hour.
Provider, owner and licence metadata remain the responsibility of an expanded
coverage-discovery pipeline.

- [OpenAQ API documentation](https://docs.openaq.org/api)
- [OpenAQ latest resource](https://docs.openaq.org/resources/latest)
- [OpenAQ rate limits](https://docs.openaq.org/using-the-api/rate-limits)

## Modeled forecasts

Air-quality forecasts are obtained through Open-Meteo and must attribute both
Open-Meteo and Copernicus Atmosphere Monitoring Service (CAMS). For Vietnam,
the global CAMS grid may be roughly 45 km and has materially coarser spatial
resolution than a street or monitoring station. The UI therefore labels these
rows `modeled` / `MODELED_ONLY` and never invents an observation.

- [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api)
- [Open-Meteo Weather API](https://open-meteo.com/en/docs)

## VN_AQI

Observed VN_AQI logic follows
[Decision 1459/QĐ-TCMT](https://cem.gov.vn/storage/news_file_attach/QD%201459%20TCMT%20ngay%2012.11.2019%20AQI.pdf).
The daily business date is 01:00–00:00 `Asia/Ho_Chi_Minh`. Any modeled result
must be described as an estimate; the outdoor decision score is a separate,
non-medical heuristic and must not be called official VN_AQI.
