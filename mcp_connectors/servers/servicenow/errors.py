"""ServiceNow connector errors."""
from __future__ import annotations

from typing import Optional


class ServiceNowError(Exception):
    retryable: bool = False

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class ServiceNowAuthError(ServiceNowError):
    """401/403 — bad credentials or insufficient (non read) roles. Not retryable."""


class ServiceNowNotFoundError(ServiceNowError):
    """404 / empty result — the record does not exist."""


class ServiceNowRateLimitError(ServiceNowError):
    """429 — exhausted retries against the rate / ACL limiter."""
    retryable = True


class ServiceNowUpstreamError(ServiceNowError):
    """5xx — ServiceNow-side failure after retries."""
    retryable = True


class ServiceNowTransportError(ServiceNowError):
    """Network/transport failure after retries."""
    retryable = True


class ServiceNowResponseError(ServiceNowError):
    """A 2xx response whose body could not be parsed as JSON."""
