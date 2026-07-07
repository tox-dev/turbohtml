"""
Shared HTTP access for the tooling: httpx2 with a tenacity retry over the transient failure modes.

Every network fetch in ``tools/`` (the benchmark corpora and the code generators that pull pinned Unicode,
PSL, and IANA data) goes through here, so a rate limit or a dropped connection retries with capped backoff
instead of failing the run. A benchmark job or a regenerate script draws a ``429`` from GitHub and the CDNs
often enough that a single unretried GET turns the whole run red.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Final

import httpx2
from tenacity import before_sleep_log, retry, retry_if_exception, stop_after_attempt, wait_exponential

__all__ = ["fetch_bytes"]

_LOGGER: Final = logging.getLogger("turbohtml.tools.httpfetch")

# The transient statuses a later attempt can clear; a 4xx like 404 or 403 is not among them, so it raises at once.
_RETRYABLE_STATUS: Final = frozenset({
    HTTPStatus.REQUEST_TIMEOUT,
    HTTPStatus.TOO_EARLY,
    HTTPStatus.TOO_MANY_REQUESTS,
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
})


def _is_retryable(exc: BaseException) -> bool:
    """Retry a dropped connection or a transient server status, never a 4xx a retry cannot fix."""
    if isinstance(exc, httpx2.TransportError):
        return True
    return isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code in _RETRYABLE_STATUS


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(5),
    wait=wait_exponential(exp_base=3, max=30),
    before_sleep=before_sleep_log(_LOGGER, logging.WARNING),
    reraise=True,
)
def fetch_bytes(url: str) -> bytes:
    """Download ``url`` and return its body, retrying the transient failures with capped backoff."""
    response = httpx2.get(url, follow_redirects=True, timeout=30.0)
    response.raise_for_status()
    return response.content
