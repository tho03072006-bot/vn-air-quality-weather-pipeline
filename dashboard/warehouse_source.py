"""Fetch the published warehouse when the deployment has no local copy.

Streamlit Community Cloud starts from a git checkout, and the warehouse is not in
git -- it is a release asset refreshed by CI. This module downloads it once per
process into the path the rest of the app already expects, so every reader below
`database_path()` keeps opening a plain local file with `read_only=True`. Nothing
about the read contract changes; only how the file arrives.

Two things here are load-bearing and easy to break:

* **The filename must not change.** DuckDB names a catalog after the file stem and
  bakes that qualifier into stored view definitions. `mart_current_conditions` and
  `mart_outdoor_decision_window` are views (audit register, finding E), so saving
  the asset under any other name makes every page that reads them raise
  `Catalog "..." does not exist`. `scripts/build_deploy_warehouse.py` enforces the
  same invariant from the other end.

* **The download must be atomic.** Streamlit reruns scripts concurrently, and a
  half-written DuckDB file is not a DuckDB file. The bytes land in a sibling
  temporary file and are moved into place only once complete.

Local development is unaffected: when the file already exists this module does
nothing at all, and with no URL configured it stays out of the way entirely.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import streamlit as st

WAREHOUSE_URL_ENV = "DEMO_WAREHOUSE_URL"
WAREHOUSE_TOKEN_ENV = "DEMO_WAREHOUSE_TOKEN"
DOWNLOAD_TIMEOUT_SECONDS = 120.0
PROBE_TIMEOUT_SECONDS = 10.0


def _secret(name: str) -> str | None:
    """Read from Streamlit secrets first, then the environment.

    Community Cloud supplies configuration through `st.secrets`; a container or a
    shell supplies it through the environment. Reading secrets raises rather than
    returning empty when no secrets file exists, which is the normal local state.
    """

    try:
        value = st.secrets.get(name)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - absent secrets file is an expected state
        value = None
    return str(value) if value else os.environ.get(name) or None


def configured_url() -> str | None:
    return _secret(WAREHOUSE_URL_ENV)


def _download(url: str, destination: Path) -> None:
    token = _secret(WAREHOUSE_TOKEN_ENV)
    headers = {}
    if token:
        # Only needed while the repository is private. A public release asset is
        # served unauthenticated, and sending a token to it is harmless but useless.
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/octet-stream"

    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, staging_name = tempfile.mkstemp(dir=str(destination.parent), suffix=".partial")
    os.close(handle)
    staging = Path(staging_name)
    try:
        with httpx.stream(
            "GET",
            url,
            headers=headers,
            follow_redirects=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        ) as response:
            response.raise_for_status()
            with staging.open("wb") as file:
                for chunk in response.iter_bytes(chunk_size=1 << 20):
                    file.write(chunk)
        staging.replace(destination)
    finally:
        staging.unlink(missing_ok=True)


@st.cache_resource(show_spinner="Đang tải dữ liệu lần đầu…")
def _fetch_once(url: str, destination: str) -> str | None:
    """Download the asset once per process. Returns None when the fetch failed.

    Failure is reported rather than raised: `require_warehouse` already renders a
    readable empty state for a missing warehouse, and a traceback on a public page
    tells a reader nothing they can act on.
    """

    target = Path(destination)
    if target.exists():
        return str(target)
    try:
        _download(url, target)
    except Exception as error:  # noqa: BLE001 - any transport failure is the same
        # outcome for a reader: no data, and a reason.
        st.error(
            "Không tải được dữ liệu đã publish. Trang sẽ hiển thị trạng thái "
            f"trống cho tới khi tải lại được. Lý do kỹ thuật: {type(error).__name__}.",
            icon=":material/cloud_off:",
        )
        return None
    return str(target)


def ensure_local_warehouse(path: Path) -> Path:
    """Return `path`, downloading the published asset first when it is absent."""

    if path.exists():
        return path
    url = configured_url()
    if not url:
        return path
    fetched = _fetch_once(url, str(path))
    return Path(fetched) if fetched else path


# --- How old is the file, as distinct from how old the data in it is ------------
#
# These are two different questions and the app could previously only answer one of
# them. The freshness badge measures the newest forecast vintage inside the
# warehouse. It says nothing about when this container obtained the warehouse.
#
# On 2026-08-16 those diverged and nothing could show it: `ensure_local_warehouse`
# returns early whenever the file exists, and a Community Cloud container keeps its
# disk, so a container that downloaded the asset once serves that copy until it is
# replaced -- while CI publishes a fresh one every six hours. The app read "Dữ liệu
# chậm · 7 giờ 59 phút" and there was no way to tell a lagging pipeline from a
# container serving yesterday's download. After a reboot the same app read "1 giờ
# 7 phút" with no code change at all.
#
# Deliberately reporting rather than reloading. Re-downloading on a TTL changes
# runtime behaviour on a live public app; making the divergence visible does not,
# and the contract stays exactly as it was -- read_only connections, atomic writes,
# and the load-bearing filename untouched. Whether to then reload is a decision that
# should be made while looking at this number, not before it exists.


@dataclass(frozen=True, slots=True)
class AssetStatus:
    """The file being served and the asset being published, side by side."""

    local_modified_utc: datetime | None
    published_modified_utc: datetime | None
    url_configured: bool
    probe_failed: bool

    @property
    def newer_asset_available(self) -> bool:
        """True when the publisher has moved on and this process has not.

        Strictly newer, with no tolerance window, because no window is needed: the
        local timestamp is when *this* container wrote the file, which is necessarily
        after the asset it copied was published. The published side only overtakes it
        when a genuinely newer asset exists.
        """

        if self.local_modified_utc is None or self.published_modified_utc is None:
            return False
        return self.published_modified_utc > self.local_modified_utc


def local_asset_modified(path: Path) -> datetime | None:
    """When the served file was last written, or None when there is no file."""

    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def probe_published_asset(url: str) -> datetime | None:
    """Read `Last-Modified` from the published asset without downloading it.

    A HEAD costs nothing next to a 12 MB body, and a release asset redirects to a
    storage host, so redirects must be followed or the header belongs to the redirect
    rather than the file. Returns None on any failure: this is diagnostic copy on an
    operator page, and a page that raises because a status probe timed out would be a
    worse outage than the one it is meant to reveal.
    """

    token = _secret(WAREHOUSE_TOKEN_ENV)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = httpx.head(
            url,
            headers=headers,
            follow_redirects=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        raw = response.headers.get("last-modified")
        if not raw:
            return None
        return parsedate_to_datetime(raw).astimezone(UTC)
    except Exception:  # noqa: BLE001 - every failure mode reports the same "unknown"
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _cached_probe(url: str) -> datetime | None:
    """Five minutes. Long enough that reruns do not hammer the release, short enough
    that an operator watching a refresh land does not think the page is stuck."""

    return probe_published_asset(url)


def asset_status(path: Path) -> AssetStatus:
    """Compare the file this process reads against the asset being published."""

    local = local_asset_modified(path)
    url = configured_url()
    if not url:
        return AssetStatus(local, None, url_configured=False, probe_failed=False)
    published = _cached_probe(url)
    return AssetStatus(
        local,
        published,
        url_configured=True,
        probe_failed=published is None,
    )
