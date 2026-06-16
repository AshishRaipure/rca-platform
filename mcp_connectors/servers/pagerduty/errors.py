"""PagerDuty connector errors."""
from __future__ import annotations

from typing import Optional


class PagerDutyError(Exception):
    retryable: bool = False

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class PagerDutyAuthError(PagerDutyError):
    """401/403 — bad or insufficiently-scoped token. Not retryable."""


class PagerDutyNotFoundError(PagerDutyError):
    """404 — resource does not exist."""


class PagerDutyRateLimitError(PagerDutyError):
    """429 — exhausted retries against the rate limiter."""
    retryable = True


class PagerDutyUpstreamError(PagerDutyError):
    """5xx — PagerDuty-side failure after retries."""
    retryable = True


class PagerDutyTransportError(PagerDutyError):
    """Network/transport failure after retries."""
    retryable = True


class PagerDutyResponseError(PagerDutyError):
    """A 2xx response whose body could not be parsed as JSON."""
