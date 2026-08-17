"""Cover the deployment-only download path, and the staleness it can hide.

The risk this module carries is not that the download breaks -- that is visible
immediately. It is that the download fires when it should not: on a developer's
machine, inside the test suite, or on top of a warehouse Airflow is writing. Three
of the first four tests below are about staying inert.

The second group covers the opposite risk, the one that actually bit: the download
correctly does *not* fire, forever, because the file already exists -- so a
long-lived container serves a copy the publisher replaced hours ago, and nothing
says so.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dashboard import warehouse_source


class _FakeResponse:
    def __init__(self, chunks: list[bytes], status: int = 200) -> None:
        self._chunks = chunks
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def iter_bytes(self, chunk_size: int = 0) -> list[bytes]:
        del chunk_size
        return self._chunks


def _fake_stream(chunks: list[bytes], status: int = 200, calls: list[str] | None = None):
    @contextlib.contextmanager
    def stream(method: str, url: str, **kwargs: object):
        del method, kwargs
        if calls is not None:
            calls.append(url)
        yield _FakeResponse(chunks, status)

    return stream


def test_existing_file_is_never_redownloaded(tmp_path: Path, monkeypatch) -> None:
    """The local path wins outright. This is what keeps local runs offline."""

    warehouse = tmp_path / "vn_air_quality_weather.duckdb"
    warehouse.write_bytes(b"original")
    calls: list[str] = []
    monkeypatch.setattr(warehouse_source.httpx, "stream", _fake_stream([b"new"], calls=calls))
    monkeypatch.setenv(warehouse_source.WAREHOUSE_URL_ENV, "https://example.invalid/w.duckdb")

    assert warehouse_source.ensure_local_warehouse(warehouse) == warehouse
    assert warehouse.read_bytes() == b"original"
    assert calls == []


def test_absent_file_without_url_stays_absent(tmp_path: Path, monkeypatch) -> None:
    """With nothing configured the module does nothing, and the caller's own empty
    state explains the missing warehouse rather than a transport error."""

    warehouse = tmp_path / "vn_air_quality_weather.duckdb"
    calls: list[str] = []
    monkeypatch.setattr(warehouse_source.httpx, "stream", _fake_stream([b"new"], calls=calls))
    monkeypatch.delenv(warehouse_source.WAREHOUSE_URL_ENV, raising=False)

    assert warehouse_source.ensure_local_warehouse(warehouse) == warehouse
    assert not warehouse.exists()
    assert calls == []


def test_download_writes_atomically_and_leaves_no_partial(tmp_path: Path, monkeypatch) -> None:
    """A half-written DuckDB file is not a DuckDB file, and Streamlit reruns scripts
    concurrently, so the bytes must appear at the destination all at once."""

    warehouse = tmp_path / "vn_air_quality_weather.duckdb"
    monkeypatch.setattr(warehouse_source.httpx, "stream", _fake_stream([b"ab", b"cd"]))

    warehouse_source._download("https://example.invalid/w.duckdb", warehouse)

    assert warehouse.read_bytes() == b"abcd"
    assert list(tmp_path.glob("*.partial")) == []


def test_failed_download_leaves_no_file_behind(tmp_path: Path, monkeypatch) -> None:
    """A failure must not leave a truncated file that later looks like a warehouse
    and fails much further downstream with a far less obvious message."""

    warehouse = tmp_path / "vn_air_quality_weather.duckdb"
    monkeypatch.setattr(warehouse_source.httpx, "stream", _fake_stream([b"ab"], status=500))

    with pytest.raises(RuntimeError):
        warehouse_source._download("https://example.invalid/w.duckdb", warehouse)

    assert not warehouse.exists()
    assert list(tmp_path.glob("*.partial")) == []


# --- Telling a stale download apart from a stale pipeline -----------------------


class _FakeHeadResponse:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers

    def raise_for_status(self) -> None:
        return None


def test_local_modified_reads_the_served_file(tmp_path: Path) -> None:
    warehouse = tmp_path / "vn_air_quality_weather.duckdb"
    warehouse.write_bytes(b"x")

    modified = warehouse_source.local_asset_modified(warehouse)

    assert modified is not None
    assert modified.tzinfo is not None
    assert abs((datetime.now(tz=UTC) - modified).total_seconds()) < 120


def test_local_modified_is_none_when_there_is_no_file(tmp_path: Path) -> None:
    """Absent is reported as unknown, not as zero. A missing file that reads as
    epoch would make every published asset look newer and warn forever."""

    assert warehouse_source.local_asset_modified(tmp_path / "absent.duckdb") is None


def test_probe_reads_last_modified_as_utc(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_head(url: str, **kwargs: object) -> _FakeHeadResponse:
        captured["url"] = url
        captured["follow_redirects"] = kwargs.get("follow_redirects")
        return _FakeHeadResponse({"last-modified": "Sun, 16 Aug 2026 07:05:17 GMT"})

    monkeypatch.setattr(warehouse_source.httpx, "head", fake_head)

    modified = warehouse_source.probe_published_asset("https://example.invalid/w.duckdb")

    assert modified == datetime(2026, 8, 16, 7, 5, 17, tzinfo=UTC)
    # A release asset redirects to a storage host. Without following it the header
    # describes the redirect, not the file.
    assert captured["follow_redirects"] is True


def test_probe_returns_none_rather_than_raising(monkeypatch) -> None:
    """This feeds diagnostic copy on an operator page. A page that crashes because a
    status probe timed out would be a worse outage than the one it reveals."""

    def fake_head(url: str, **kwargs: object) -> _FakeHeadResponse:
        del url, kwargs
        raise TimeoutError("no route to host")

    monkeypatch.setattr(warehouse_source.httpx, "head", fake_head)

    assert warehouse_source.probe_published_asset("https://example.invalid/w.duckdb") is None


def test_probe_returns_none_when_the_header_is_absent(monkeypatch) -> None:
    monkeypatch.setattr(
        warehouse_source.httpx,
        "head",
        lambda url, **kwargs: _FakeHeadResponse({}),
    )

    assert warehouse_source.probe_published_asset("https://example.invalid/w.duckdb") is None


def test_newer_asset_is_flagged_only_when_the_publisher_moved_on() -> None:
    """The local timestamp is when this container wrote the file, which is always
    after the asset it copied was published. The published side overtaking it is
    therefore evidence of a new asset, not of clock skew -- which is why no tolerance
    window is applied and none is needed."""

    downloaded = datetime(2026, 8, 16, 5, 0, tzinfo=UTC)

    stale = warehouse_source.AssetStatus(
        local_modified_utc=downloaded,
        published_modified_utc=downloaded + timedelta(hours=6),
        url_configured=True,
        probe_failed=False,
    )
    current = warehouse_source.AssetStatus(
        local_modified_utc=downloaded,
        published_modified_utc=downloaded - timedelta(minutes=1),
        url_configured=True,
        probe_failed=False,
    )

    assert stale.newer_asset_available
    assert not current.newer_asset_available


def test_unknown_timestamps_never_claim_staleness() -> None:
    """Two unknowns must not add up to an accusation. A failed probe is reported as
    unknown on the page, and must not render as 'a newer asset exists'."""

    known = datetime(2026, 8, 16, 5, 0, tzinfo=UTC)

    assert not warehouse_source.AssetStatus(known, None, True, True).newer_asset_available
    assert not warehouse_source.AssetStatus(None, known, True, False).newer_asset_available
    assert not warehouse_source.AssetStatus(None, None, False, False).newer_asset_available


def test_status_makes_no_request_when_no_url_is_configured(tmp_path: Path, monkeypatch) -> None:
    """Local development must stay offline. Nothing here may reach the network just
    because an operator opened the pipeline page."""

    warehouse = tmp_path / "vn_air_quality_weather.duckdb"
    warehouse.write_bytes(b"x")
    monkeypatch.delenv(warehouse_source.WAREHOUSE_URL_ENV, raising=False)

    def refuse(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("probed the network with no URL configured")

    monkeypatch.setattr(warehouse_source.httpx, "head", refuse)

    status = warehouse_source.asset_status(warehouse)

    assert status.url_configured is False
    assert status.probe_failed is False
    assert status.published_modified_utc is None
    assert status.local_modified_utc is not None
