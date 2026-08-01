ARG AIRFLOW_IMAGE_NAME=apache/airflow:3.3.0-python3.11
FROM ${AIRFLOW_IMAGE_NAME}

COPY --chown=airflow:root pyproject.toml README.md /opt/project/
COPY --chown=airflow:root src /opt/project/src

RUN pip install --no-cache-dir apache-airflow==3.3.0 /opt/project

WORKDIR /opt/project
