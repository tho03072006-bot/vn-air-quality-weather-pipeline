# Vietnam Air Quality & Weather Data Pipeline

A learning-focused data engineering project that collects hourly weather and
air-quality data for Hanoi, Ho Chi Minh City, and Da Nang.

## Project status

Milestone 1 — repository foundation in progress.

No production pipeline has been implemented yet. OpenAQ station and pollutant
coverage for the three target cities must be validated before the analytical
schema is finalized.

## MVP architecture

```text
Open-Meteo + OpenAQ APIs
            |
            v
    Apache Airflow 3
            |
            v
    Raw JSON in AWS S3
            |
            v
     Python + dlt
            |
            v
     DuckDB warehouse
            |
            v
   dbt models and tests
            |
            v
  Streamlit analytics dashboard
```

## MVP scope

- Collect hourly weather data from Open-Meteo.
- Collect available air-quality measurements from OpenAQ.
- Preserve immutable raw API responses in partitioned AWS S3 objects.
- Load data incrementally into DuckDB with rerun-safe natural keys.
- Build staging, fact, dimension, and analytics models with dbt Core.
- Orchestrate daily runs and historical backfills with Apache Airflow 3.
- Present validated analytics marts through Streamlit.
- Run offline tests and dbt builds in GitHub Actions.

## Target cities

- Hanoi
- Ho Chi Minh City
- Da Nang

## Important limitations

- OpenAQ station and pollutant coverage varies by city and time period.
- Correlation does not demonstrate causation.
- This project is for learning and analytical purposes and does not replace
  medical or public-health advice.
- No claim about Hanoi's annual global pollution ranking is included because
  the available IQAir evidence describes time-specific, real-time rankings.

## Security

Secrets, local DuckDB files, raw data, logs, virtual environments, and generated
dbt artifacts must not be committed to Git.

Use `.env.example` only as a configuration template. Store real local values in
an ignored `.env` file.

## Next milestone

Validate current Open-Meteo and OpenAQ API contracts, OpenAQ station coverage,
pollutant availability, units, timestamps, and pagination behavior before
writing ingestion code.