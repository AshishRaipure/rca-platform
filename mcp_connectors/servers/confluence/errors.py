"""Confluence connector errors."""
from __future__ import annotations

from typing import Optional


class ConfluenceError(Exception):
    retryable: bool = False

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class ConfluenceAuthError(ConfluenceError):
    """401/403 — bad credentials or insufficient (non read) permissions. Not retryable."""


class ConfluenceNotFoundError(ConfluenceError):
    """404 / empty result — the content does not exist or isn't visible."""


class ConfluenceRateLimitError(ConfluenceError):
    """429 — exhausted retries against the rate limiter."""
    retryable = True


class ConfluenceUpstreamError(ConfluenceError):
    """5xx — Confluence-side failure after retries."""
    retryable = True


class ConfluenceTransportError(ConfluenceError):
    """Network/transport failure after retries."""
    retryable = True


class ConfluenceResponseError(ConfluenceError):
    """A 2xx response whose body could not be parsed as JSON."""
