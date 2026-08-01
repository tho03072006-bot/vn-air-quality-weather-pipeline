import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from vn_air_quality_weather.storage.raw_json import (
    LocalRawJsonStore,
    S3RawJsonStore,
    build_raw_envelope,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_: object) -> None:
        self.objects[(Bucket, Key)] = Body


def _arguments(payload: dict[str, object]) -> dict[str, object]:
    return {
        "source": "open_meteo_air_quality",
        "city_key": "ho_chi_minh",
        "ingestion_date": date(2026, 8, 1),
        "run_id": "scheduled__2026-07-27T00:00:00+00:00",
        "payload": payload,
    }


def test_local_store_uses_traceable_partitioned_path(tmp_path: Path) -> None:
    store = LocalRawJsonStore(tmp_path)
    result = store.write(**_arguments({"city": "Hồ Chí Minh", "hours": 24}))

    path = Path(result.location)
    assert result.created is True
    assert path.exists()
    assert (
        path.relative_to(tmp_path)
        .as_posix()
        .startswith("raw/open_meteo_air_quality/city=ho_chi_minh/ingestion_date=2026-08-01/")
    )
    assert "run_id=scheduled__2026-07-27T00-00-00-00-00" in path.name
    assert json.loads(path.read_text(encoding="utf-8"))["hours"] == 24


def test_same_payload_and_run_is_idempotent(tmp_path: Path) -> None:
    store = LocalRawJsonStore(tmp_path)
    first = store.write(**_arguments({"hours": [1, 2, 3]}))
    second = store.write(**_arguments({"hours": [1, 2, 3]}))

    assert first.created is True
    assert second.created is False
    assert first.location == second.location
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_changed_payload_creates_new_version(tmp_path: Path) -> None:
    store = LocalRawJsonStore(tmp_path)
    first = store.write(**_arguments({"value": 10}))
    second = store.write(**_arguments({"value": 11}))
    assert first.location != second.location
    assert len(list(tmp_path.rglob("*.json"))) == 2


def test_s3_store_is_idempotent() -> None:
    client = FakeS3Client()
    store = S3RawJsonStore("test-bucket", client)
    first = store.write(**_arguments({"value": 10}))
    second = store.write(**_arguments({"value": 10}))

    assert first.created is True
    assert second.created is False
    assert first.location.startswith("s3://test-bucket/raw/open_meteo_air_quality/")
    assert len(client.objects) == 1


def test_raw_envelope_contains_required_lineage() -> None:
    start = datetime(2026, 7, 27, tzinfo=UTC)
    end = datetime(2026, 7, 28, tzinfo=UTC)
    envelope = build_raw_envelope(
        source="openaq",
        city_key="hanoi",
        requested_at=start,
        interval_start=start,
        interval_end=end,
        run_id="test-run",
        request_parameters={"sensor_id": 1},
        response={"results": []},
    )
    assert envelope["metadata"]["interval_start"] == "2026-07-27T00:00:00Z"
    assert envelope["metadata"]["airflow_run_id"] == "test-run"
    assert envelope["response"] == {"results": []}


def test_rejects_unsafe_partition_value(tmp_path: Path) -> None:
    store = LocalRawJsonStore(tmp_path)
    with pytest.raises(ValueError, match="city_key"):
        store.write(
            source="open_meteo_air_quality",
            city_key="../da_nang",
            ingestion_date=date(2026, 8, 1),
            run_id="test",
            payload={},
        )
