"""Shared HTTP retry policy for the upstream data-source clients.

Open-Meteo and OpenAQ are public services that throttle aggressively and
occasionally return transient gateway errors. Without a retry policy a single
429 fails the whole Airflow task, so every client routes its requests through
``request_with_retry``.

The module is deliberately not called ``http``: a module of that name inside
the package would be a confusing near-miss for the standard library module that
httpx itself imports.
"""

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff with jitter."""

    max_attempts: int = 5
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 30.0
    jitter_ratio: float = 0.25

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff_base_seconds <= 0:
            raise ValueError("backoff_base_seconds must be positive")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds must be >= backoff_base_seconds")
        if not 0.0 <= self.jitter_ratio < 1.0:
            raise ValueError("jitter_ratio must be in [0, 1)")

    def delay_for(self, attempt: int, retry_after_seconds: float | None = None) -> float:
        """Return the sleep duration before the given 1-based attempt is retried."""

        if retry_after_seconds is not None and retry_after_seconds >= 0:
            return min(retry_after_seconds, self.backoff_max_seconds)

        exponential = self.backoff_base_seconds * (2 ** (attempt - 1))
        capped = min(exponential, self.backoff_max_seconds)
        jitter = capped * self.jitter_ratio
        return max(0.0, capped + random.uniform(-jitter, jitter))


DEFAULT_RETRY_POLICY = RetryPolicy()


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], None] = time.sleep,
    **request_kwargs: Any,
) -> httpx.Response:
    """Issue an HTTP request, retrying transient transport and status failures.

    Non-retryable responses (for example 401 or 404) raise immediately through
    ``raise_for_status`` so configuration errors fail fast instead of burning
    the retry budget.
    """

    last_error: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            response = client.request(method, url, **request_kwargs)
        except httpx.TransportError as error:
            last_error = error
            if attempt == policy.max_attempts:
                break
            delay = policy.delay_for(attempt)
            LOGGER.warning(
                "http_retry url=%s attempt=%d/%d reason=%s sleep=%.2fs",
                url,
                attempt,
                policy.max_attempts,
                type(error).__name__,
                delay,
            )
            sleep(delay)
            continue

        if response.status_code not in RETRYABLE_STATUS_CODES:
            response.raise_for_status()
            return response

        last_error = httpx.HTTPStatusError(
            f"{response.status_code} from {url}",
            request=response.request,
            response=response,
        )
        if attempt == policy.max_attempts:
            response.read()
            response.close()
            break

        delay = policy.delay_for(attempt, parse_retry_after(response.headers))
        LOGGER.warning(
            "http_retry url=%s attempt=%d/%d status=%d sleep=%.2fs",
            url,
            attempt,
            policy.max_attempts,
            response.status_code,
            delay,
        )
        response.close()
        sleep(delay)

    if last_error is None:  # pragma: no cover - defensive, the loop always records a failure
        raise RuntimeError(f"request_with_retry exhausted attempts without an error for {url}")
    raise last_error


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """Return the Retry-After delay in seconds, accepting both header formats."""

    raw_value = headers.get("Retry-After") or headers.get("retry-after")
    if raw_value is None:
        return None

    candidate = raw_value.strip()
    try:
        return max(0.0, float(candidate))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(candidate)
    except (TypeError, ValueError):
        return None
    if retry_at is None:
        return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
