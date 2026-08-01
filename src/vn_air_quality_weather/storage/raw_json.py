import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from botocore.exceptions import ClientError

_SAFE_PARTITION_VALUE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SAFE_RUN_ID = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True, slots=True)
class RawWriteResult:
    location: str
    content_sha256: str
    created: bool


class RawJsonStore(Protocol):
    def write(
        self,
        *,
        source: str,
        city_key: str,
        ingestion_date: date,
        run_id: str,
        payload: dict[str, Any],
    ) -> RawWriteResult: ...


class LocalRawJsonStore:
    """Persist canonical raw JSON with the same keys used by the S3 backend."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(
        self,
        *,
        source: str,
        city_key: str,
        ingestion_date: date,
        run_id: str,
        payload: dict[str, Any],
    ) -> RawWriteResult:
        content = _canonical_bytes(payload)
        content_sha256 = hashlib.sha256(content).hexdigest()
        key = _object_key(source, city_key, ingestion_date, run_id, content_sha256)
        destination = self._root / Path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            return RawWriteResult(str(destination), content_sha256, created=False)

        # Short name avoids the default Windows path-length limit in pytest folders.
        temporary_path = destination.parent / f".tmp-{uuid4().hex[:12]}"
        try:
            temporary_path.write_bytes(content)
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)

        return RawWriteResult(str(destination), content_sha256, created=True)


class S3RawJsonStore:
    """Persist immutable raw JSON objects without exposing AWS credentials."""

    def __init__(self, bucket: str, s3_client: Any, prefix: str = "raw") -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket must not be empty")
        self._bucket = bucket
        self._client = s3_client
        self._prefix = prefix.strip("/")

    def write(
        self,
        *,
        source: str,
        city_key: str,
        ingestion_date: date,
        run_id: str,
        payload: dict[str, Any],
    ) -> RawWriteResult:
        content = _canonical_bytes(payload)
        content_sha256 = hashlib.sha256(content).hexdigest()
        relative_key = _object_key(
            source, city_key, ingestion_date, run_id, content_sha256, include_raw=False
        )
        key = f"{self._prefix}/{relative_key}" if self._prefix else relative_key

        if self._exists(key):
            return RawWriteResult(f"s3://{self._bucket}/{key}", content_sha256, created=False)

        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content,
            ContentType="application/json",
            Metadata={"content-sha256": content_sha256},
        )
        return RawWriteResult(f"s3://{self._bucket}/{key}", content_sha256, created=True)

    def _exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True


def build_raw_envelope(
    *,
    source: str,
    city_key: str,
    requested_at: datetime,
    interval_start: datetime,
    interval_end: datetime,
    run_id: str,
    request_parameters: dict[str, Any],
    response: dict[str, Any],
    pipeline_version: str = "0.1.0",
) -> dict[str, Any]:
    """Attach traceability metadata while keeping the exact API response intact."""

    return {
        "metadata": {
            "source": source,
            "city_key": city_key,
            "requested_at": _iso_z(requested_at),
            "ingestion_timestamp_utc": _iso_z(datetime.now(UTC)),
            "interval_start": _iso_z(interval_start),
            "interval_end": _iso_z(interval_end),
            "airflow_run_id": run_id,
            "api_request_parameters": request_parameters,
            "pipeline_version": pipeline_version,
        },
        "response": response,
    }


def _object_key(
    source: str,
    city_key: str,
    ingestion_date: date,
    run_id: str,
    content_sha256: str,
    *,
    include_raw: bool = True,
) -> str:
    _validate_partition_value("source", source)
    _validate_partition_value("city_key", city_key)
    safe_run_id = _SAFE_RUN_ID.sub("-", run_id).strip("-._") or "manual"
    filename = f"run_id={safe_run_id}-{content_sha256[:12]}.json"
    parts = [
        source,
        f"city={city_key}",
        f"ingestion_date={ingestion_date.isoformat()}",
        filename,
    ]
    if include_raw:
        parts.insert(0, "raw")
    return "/".join(parts)


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _validate_partition_value(name: str, value: str) -> None:
    if not _SAFE_PARTITION_VALUE.fullmatch(value):
        raise ValueError(
            f"{name} must contain only lowercase letters, numbers, hyphens, and underscores"
        )


def _iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Raw metadata timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
