"""Retry policy for B站 risk-control responses (HTTP 412 / API -352).

Shared by every module that talks to bilibili_api, so the backoff policy lives
in exactly one place. Only idempotent GET-style calls should be wrapped.
"""
from __future__ import annotations

import asyncio

from bilibili_api.exceptions import NetworkException, ResponseCodeException

from bilibili_podcast.logger import get_logger

logger = get_logger()

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
_RETRYABLE_HTTP_STATUS = frozenset({412, 429})
_RETRYABLE_API_CODES = frozenset({-352, -412})


def is_retryable(exc: Exception) -> bool:
    """Whether ``exc`` is a risk-control response worth retrying."""
    if isinstance(exc, NetworkException):
        return exc.status in _RETRYABLE_HTTP_STATUS
    if isinstance(exc, ResponseCodeException):
        return exc.code in _RETRYABLE_API_CODES
    return False


async def with_retry(
    call,
    *,
    attempts: int = RETRY_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY,
    sleep=asyncio.sleep,
):
    """Await ``call()``, retrying risk-control errors with exponential backoff."""
    for attempt in range(attempts):
        try:
            return await call()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            if attempt == attempts - 1:
                logger.error(f"===> risk control persisted after {attempts} attempts: {exc}")
                raise
            delay = base_delay * (2**attempt)
            logger.warning(f"===> risk control, retrying in {delay:.0f}s: {exc}")
            await sleep(delay)
