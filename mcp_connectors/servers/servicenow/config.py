"""ServiceNow connector configuration.

The service account / OAuth token referenced here MUST hold read-only roles. That is the
credential-layer half of the read-only guarantee; the code-layer half is that the client only
issues GETs and only read tools are registered.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ServiceNowConfig(BaseModel):
    # deployment-specific; resolved from env at composition if left blank
    instance_url: str = ""  # e.g. "https://acme.service-now.com"
    instance_url_env: str = "SERVICENOW_INSTANCE_URL"

    # auth: secrets referenced by env-var NAME, never stored here
    auth_mode: Literal["oauth_bearer", "basic"] = "oauth_bearer"
    token_env: str = "SERVICENOW_READONLY_TOKEN"          # for oauth_bearer
    username_env: str = "SERVICENOW_READONLY_USERNAME"    # for basic
    password_env: str = "SERVICENOW_READONLY_PASSWORD"    # for basic

    request_timeout_s: float = 12.0
    max_retries: int = Field(default=3, ge=0)
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0

    # pagination guards (bound cost / blast radius)
    page_limit: int = Field(default=50, ge=1, le=1000)
    max_items: int = Field(default=200, ge=1)
    max_pages: int = Field(default=10, ge=1)

    # "all" returns {display_value, value} per field (labels + sys_ids); "true" = labels; "false" = raw
    default_display_value: Literal["all", "true", "false"] = "all"

    user_agent: str = "rca-platform-servicenow-connector/1.0 (read-only)"
