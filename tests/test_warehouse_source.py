"""Cover the deployment-only download path.

The risk this module carries is not that the download breaks -- that is visible
immediately. It is that the download fires when it should not: on a developer's
machine, inside the test suite, or on top of a warehouse Airflow is writing. Three
of the four tests below are about staying inert.
"""

from __future__ import annotations

import contextlib
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
