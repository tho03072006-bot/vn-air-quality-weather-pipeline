"""Deprecated alias for :mod:`vn_air_quality_weather.retry`.

This module was renamed so that the package does not carry a near-miss for the
standard library ``http`` package. Delete it once nothing imports it:

    git rm src/vn_air_quality_weather/http.py
"""

from vn_air_quality_weather.retry import (  # noqa: F401
    DEFAULT_RETRY_POLICY,
    RETRYABLE_STATUS_CODES,
    RetryPolicy,
    parse_retry_after,
    request_with_retry,
)

__all__ = [
    "DEFAULT_RETRY_POLICY",
    "RETRYABLE_STATUS_CODES",
    "RetryPolicy",
    "parse_retry_after",
    "request_with_retry",
]
