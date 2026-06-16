"""Authentication & authorization primitives.

Authentication: OIDC JWT bearer tokens, verified against the IdP's JWKS (signature + issuer +
audience + expiry). Authorization: RBAC via roles + ABAC via team/service scopes carried on the
principal and pushed into Postgres RLS (see db/engine.apply_scope).

FastAPI dependency wiring lives in deps.py; this module is pure logic + the verifier.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Optional, Protocol

from pydantic import BaseModel, Field

from services.api.config import ApiSettings
from services.api.errors import Unauthorized


class Role(str, Enum):
    viewer = "viewer"        # read investigations in scope
    responder = "responder"  # create/run investigations
    approver = "approver"    # record approval decisions on the review gate
    admin = "admin"          # cross-team access + administration


class Principal(BaseModel):
    subject: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    roles: list[Role] = Field(default_factory=list)
    team_ids: list[str] = Field(default_factory=list)
    service_ids: list[str] = Field(default_factory=list)
    claims: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        return Role.admin in self.roles

    def has_any_role(self, roles: Iterable[Role]) -> bool:
        wanted = set(roles)
        return self.is_admin or bool(wanted.intersection(self.roles))

    @property
    def scope(self) -> dict[str, Any]:
        """The ABAC envelope handed to the persistence layer (RLS GUCs) and the orchestrator."""
        return {
            "user_id": self.subject,
            "team_ids": list(self.team_ids),
            "service_ids": list(self.service_ids),
            "is_admin": self.is_admin,
        }

    @property
    def primary_team(self) -> Optional[str]:
        return self.team_ids[0] if self.team_ids else None


class VerifiedToken(BaseModel):
    subject: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    team_ids: list[str] = Field(default_factory=list)
    service_ids: list[str] = Field(default_factory=list)
    claims: dict[str, Any] = Field(default_factory=dict)


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> VerifiedToken:
        """Validate a bearer token and return its claims, or raise Unauthorized."""
        ...


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


class OIDCTokenVerifier:
    """Verifies RS256 OIDC tokens against the IdP JWKS. PyJWT is imported lazily."""

    def __init__(self, settings: ApiSettings) -> None:
        self._s = settings
        self._jwk_client: Any = None

    def _client(self) -> Any:
        if self._jwk_client is None:
            import jwt  # lazy
            self._jwk_client = jwt.PyJWKClient(self._s.oidc_jwks_url)
        return self._jwk_client

    async def verify(self, token: str) -> VerifiedToken:
        import jwt  # lazy
        try:
            signing_key = self._client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, signing_key, algorithms=["RS256"],
                audience=self._s.oidc_audience, issuer=self._s.oidc_issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception as exc:  # invalid signature/expiry/claims
            raise Unauthorized("invalid or expired token", code="invalid_token") from exc

        return VerifiedToken(
            subject=str(claims.get("sub")),
            email=claims.get(self._s.email_claim),
            display_name=claims.get(self._s.name_claim),
            roles=_as_list(claims.get(self._s.roles_claim)),
            team_ids=_as_list(claims.get(self._s.teams_claim)),
            service_ids=_as_list(claims.get(self._s.services_claim)),
            claims=claims,
        )


def map_verified_to_principal(verified: VerifiedToken) -> Principal:
    roles: list[Role] = []
    for r in verified.roles:
        try:
            roles.append(Role(r))
        except ValueError:
            continue  # ignore unknown roles
    return Principal(
        subject=verified.subject, email=verified.email, display_name=verified.display_name,
        roles=roles, team_ids=verified.team_ids, service_ids=verified.service_ids,
        claims=verified.claims,
    )
