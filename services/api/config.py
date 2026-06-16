"""API service settings (resolved from environment)."""
from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field


class ApiSettings(BaseModel):
    environment: str = "production"

    # database
    db_dsn: str = ""  # postgresql+asyncpg://...

    # OIDC / JWT
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    roles_claim: str = "roles"
    teams_claim: str = "team_ids"
    services_claim: str = "service_ids"
    email_claim: str = "email"
    name_claim: str = "name"

    # API behavior
    cors_origins: list[str] = Field(default_factory=list)
    default_page_size: int = 25
    max_page_size: int = 100
    sse_poll_interval_s: float = 2.0
    sse_max_polls: int = 150

    @classmethod
    def from_env(cls) -> "ApiSettings":
        def _csv(name: str) -> list[str]:
            raw = os.environ.get(name, "")
            return [p.strip() for p in raw.split(",") if p.strip()]

        return cls(
            environment=os.environ.get("APP_ENV", "production"),
            db_dsn=os.environ.get("DATABASE_DSN", ""),
            oidc_issuer=os.environ.get("OIDC_ISSUER", ""),
            oidc_audience=os.environ.get("OIDC_AUDIENCE", ""),
            oidc_jwks_url=os.environ.get("OIDC_JWKS_URL", ""),
            roles_claim=os.environ.get("OIDC_ROLES_CLAIM", "roles"),
            teams_claim=os.environ.get("OIDC_TEAMS_CLAIM", "team_ids"),
            services_claim=os.environ.get("OIDC_SERVICES_CLAIM", "service_ids"),
            cors_origins=_csv("CORS_ORIGINS"),
        )
