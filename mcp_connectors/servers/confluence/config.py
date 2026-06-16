"""Confluence connector configuration.

The token / credentials referenced here MUST be read-only (a read-scoped API token or a user with
read-only space permissions). That is the credential-layer half of the read-only guarantee; the
code-layer half is that the client only issues GETs and only read tools are registered.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConfluenceConfig(BaseModel):
    # deployment-specific; resolved from env at composition if left blank
    base_url: str = ""  # e.g. "https://acme.atlassian.net/wiki" (Cloud) or the Server base
    base_url_env: str = "CONFLUENCE_BASE_URL"
    api_base: str = "/rest/api"  # Confluence REST v1

    # auth: secrets referenced by env-var NAME, never stored here
    auth_mode: Literal["basic", "bearer"] = "basic"
    username_env: str = "CONFLUENCE_EMAIL"        # for basic (Cloud: account email)
    token_env: str = "CONFLUENCE_API_TOKEN"       # API token (basic password) or bearer token

    request_timeout_s: float = 12.0
    max_retries: int = Field(default=3, ge=0)
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0

    # pagination guards (bound cost / blast radius)
    page_limit: int = Field(default=25, ge=1, le=100)
    max_items: int = Field(default=200, ge=1)
    max_pages: int = Field(default=20, ge=1)

    default_body_format: Literal["storage", "view"] = "storage"
    snippet_max_chars: int = Field(default=800, ge=80)

    user_agent: str = "rca-platform-confluence-connector/1.0 (read-only)"
