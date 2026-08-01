from typing import Any

import httpx
import pytest

from vn_air_quality_weather.retry import (
    RetryPolicy,
    parse_retry_after,
    request_with_retry,
)

FAST_POLICY = RetryPolicy(max_attempts=4, backoff_base_seconds=0.01, backoff_max_seconds=0.05)


class RecordingSleep:
    """Stand-in for time.sleep so retry tests stay instant and assertable."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_retries_then_succeeds_after_transient_status() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    sleep = RecordingSleep()
    with _client(handler) as client:
        response = request_with_retry(
            client, "GET", "https://example.test/data", policy=FAST_POLICY, sleep=sleep
        )

    assert response.json() == {"ok": True}
    assert len(attempts) == 3
    assert len(sleep.delays) == 2


def test_gives_up_after_max_attempts() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429)

    sleep = RecordingSleep()
    with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            request_with_retry(
                client, "GET", "https://example.test/data", policy=FAST_POLICY, sleep=sleep
            )

    assert len(calls) == FAST_POLICY.max_attempts
    assert len(sleep.delays) == FAST_POLICY.max_attempts - 1


def test_does_not_retry_client_errors() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401)

    sleep = RecordingSleep()
    with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            request_with_retry(
                client, "GET", "https://example.test/data", policy=FAST_POLICY, sleep=sleep
            )

    assert calls == [1]
    assert sleep.delays == []


def test_retries_transport_errors() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 2:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"ok": True})

    sleep = RecordingSleep()
    with _client(handler) as client:
        response = request_with_retry(
            client, "GET", "https://example.test/data", policy=FAST_POLICY, sleep=sleep
        )

    assert response.status_code == 200
    assert len(calls) == 2


def test_retry_after_header_overrides_backoff() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "2"})

    sleep = RecordingSleep()
    policy = RetryPolicy(max_attempts=2, backoff_base_seconds=0.01, backoff_max_seconds=10.0)
    with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            request_with_retry(
                client, "GET", "https://example.test/data", policy=policy, sleep=sleep
            )

    assert sleep.delays == [2.0]


def test_retry_after_is_capped_by_backoff_max() -> None:
    policy = RetryPolicy(backoff_max_seconds=5.0)
    assert policy.delay_for(1, retry_after_seconds=600.0) == 5.0


def test_backoff_grows_and_stays_within_jitter_band() -> None:
    policy = RetryPolicy(backoff_base_seconds=1.0, backoff_max_seconds=100.0, jitter_ratio=0.25)
    for attempt in range(1, 5):
        expected = 2 ** (attempt - 1)
        delay = policy.delay_for(attempt)
        assert expected * 0.75 <= delay <= expected * 1.25


def test_parse_retry_after_accepts_http_date_and_garbage() -> None:
    assert parse_retry_after({"Retry-After": "12"}) == 12.0
    assert parse_retry_after({"retry-after": "0"}) == 0.0
    assert parse_retry_after({}) is None
    assert parse_retry_after({"Retry-After": "not-a-date"}) is None

    http_date = parse_retry_after({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    assert http_date == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"backoff_base_seconds": 0},
        {"backoff_base_seconds": 10.0, "backoff_max_seconds": 1.0},
        {"jitter_ratio": 1.0},
    ],
)
def test_invalid_policies_are_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)
