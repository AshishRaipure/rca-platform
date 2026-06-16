"""System router: health probes and identity echo."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from services.api.auth import Principal
from services.api.deps import get_principal
from services.api.schemas import MeResponse

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ready"}


@router.get("/v1/me", response_model=MeResponse)
async def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    return MeResponse(
        subject=principal.subject, email=principal.email, display_name=principal.display_name,
        roles=[r.value for r in principal.roles], team_ids=principal.team_ids,
        service_ids=principal.service_ids,
    )
