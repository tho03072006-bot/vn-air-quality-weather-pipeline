ARG AIRFLOW_IMAGE_NAME=apache/airflow:3.3.0-python3.11
FROM ${AIRFLOW_IMAGE_NAME}

COPY --chown=airflow:root pyproject.toml README.md /opt/project/
COPY --chown=airflow:root src /opt/project/src

# The `etl` extra is required, not optional, for this image. Both DAGs import
# forecast_pipeline / pipeline, which import the dlt loader at module scope, so
# without it the DagBag fails to import either DAG and Airflow reports the whole
# folder as broken rather than reporting a missing package.
RUN pip install --no-cache-dir apache-airflow==3.3.0 "/opt/project[etl]"

WORKDIR /opt/project
