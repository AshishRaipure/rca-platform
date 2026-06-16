"""PagerDuty connector configuration.

The token named by ``api_token_env`` MUST be a PagerDuty read-only API key. That is the
credential-layer half of the read-only guarantee; the code-layer half is that the client only
issues GETs and only read tools are registered.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PagerDutyConfig(BaseModel):
    base_url: str = "https://api.pagerduty.com"
    api_version: str = "2"

    # secrets are referenced by env-var NAME; the value is resolved at composition, never stored here
    api_token_env: str = "PAGERDUTY_READONLY_API_TOKEN"
    webhook_signing_secret_env: str = "PAGERDUTY_WEBHOOK_SECRET"

    request_timeout_s: float = 10.0
    max_retries: int = Field(default=3, ge=0)
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0

    # pagination guards (bound cost / blast radius)
    page_limit: int = Field(default=50, ge=1, le=100)
    max_items: int = Field(default=200, ge=1)
    max_pages: int = Field(default=10, ge=1)

    user_agent: str = "rca-platform-pagerduty-connector/1.0 (read-only)"
