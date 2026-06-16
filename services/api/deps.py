"""FastAPI dependency wiring.

``AppDeps`` is built once at startup and stashed on ``app.state``; the dependencies here read it
per request. Authentication (get_principal) and RBAC (require_roles) live here because they touch
the verifier from app state; the pure auth logic is in auth.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from fastapi import Depends, Request

from db.repositories import UnitOfWork
from services.api.auth import Principal, Role, TokenVerifier, map_verified_to_principal
from services.api.config import ApiSettings
from services.api.errors import Forbidden, ServiceUnavailable, Unauthorized
from services.orchestrator.client import OrchestratorClient

UnitOfWorkFactory = Callable[[dict[str, Any]], UnitOfWork]


@dataclass
class AppDeps:
    settings: ApiSettings
    token_verifier: TokenVerifier
    uow_factory: UnitOfWorkFactory
    orchestrator: Optional[OrchestratorClient] = None
    aclose: Optional[Callable[[], Awaitable[None]]] = None  # called on app shutdown


def get_app_deps(request: Request) -> AppDeps:
    return request.app.state.deps


def _bearer(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("missing bearer token", code="missing_token")
    return token.strip()


async def get_principal(request: Request,
                        deps: AppDeps = Depends(get_app_deps)) -> Principal:
    token = _bearer(request)
    verified = await deps.token_verifier.verify(token)  # raises Unauthorized on failure
    return map_verified_to_principal(verified)


def require_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_any_role(roles):
            raise Forbidden(
                "insufficient role for this operation", code="insufficient_role")
        return principal
    return _dep


def require_orchestrator(deps: AppDeps = Depends(get_app_deps)) -> OrchestratorClient:
    if deps.orchestrator is None:
        raise ServiceUnavailable("orchestrator is not configured", code="orchestrator_unavailable")
    return deps.orchestrator
