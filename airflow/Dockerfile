ARG AIRFLOW_IMAGE_NAME=apache/airflow:3.3.0-python3.11
FROM ${AIRFLOW_IMAGE_NAME}

# The ODBC driver the serving publish needs. pyodbc is a Python binding around a
# system driver, not a self-contained client, so `pip install pyodbc` succeeds in the
# image and then fails at run time with "Can't open lib ... file not found" -- an
# error that names a shared object and not the missing package. Installed before the
# project so a change to src/ does not invalidate this layer.
#
# Pinned to Debian 12 because that is what apache/airflow:3.3.0-python3.11 is built
# on. Microsoft publishes one repository per release, and pointing at the wrong one
# installs a driver linked against the wrong libc.
USER root
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl gnupg ca-certificates; \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg; \
    echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] \
https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list; \
    apt-get update; \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18; \
    apt-get purge -y --auto-remove gnupg; \
    rm -rf /var/lib/apt/lists/*
USER airflow

COPY --chown=airflow:root pyproject.toml README.md /opt/project/
COPY --chown=airflow:root src /opt/project/src

# The `etl` extra is required, not optional, for this image. Both DAGs import
# forecast_pipeline / pipeline, which import the dlt loader at module scope, so
# without it the DagBag fails to import either DAG and Airflow reports the whole
# folder as broken rather than reporting a missing package.
RUN pip install --no-cache-dir apache-airflow==3.3.0 "/opt/project[etl]"

WORKDIR /opt/project
