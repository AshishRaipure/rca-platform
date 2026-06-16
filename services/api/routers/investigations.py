"""Investigations API.

POST creates the incident + investigation (one transaction with its audit row), then hands the
workflow to the orchestrator. No endpoint here mutates production: starting an *investigation* is
read/reasoning only, and it pauses at the human-review gate.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from contracts.enums import SourceSystem
from contracts.models import NormalizedIncident
from services.api.auth import Principal, Role
from services.api.deps import AppDeps, get_app_deps, get_principal, require_orchestrator, require_roles
from services.api.errors import BadRequest, NotFound
from services.api.schemas import (
    CreateInvestigationRequest,
    InvestigationDetail,
    InvestigationSummary,
    Page,
    decode_cursor,
    encode_cursor,
)
from services.orchestrator.client import OrchestratorClient

router = APIRouter(prefix="/v1/investigations", tags=["investigations"])


def _summary(inv: Any) -> InvestigationSummary:
    return InvestigationSummary(
        id=inv.id, status=inv.status, severity=inv.severity, title=inv.title,
        recommended_triage=(inv.state or {}).get("recommended_triage"),
        created_at=inv.created_at, updated_at=inv.updated_at,
    )


@router.post("", status_code=201, response_model=InvestigationSummary)
async def create_investigation(
    body: CreateInvestigationRequest,
    response: Response,
    principal: Principal = Depends(require_roles(Role.responder, Role.approver)),
    deps: AppDeps = Depends(get_app_deps),
    orchestrator: OrchestratorClient = Depends(require_orchestrator),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> InvestigationSummary:
    team_id = body.team_id or principal.primary_team
    if not team_id:
        raise BadRequest("no team_id provided and caller has no team", code="missing_team")
    if not principal.is_admin and team_id not in principal.team_ids:
        raise BadRequest("cannot create an investigation outside your team scope",
                         code="team_scope")

    incident = NormalizedIncident(
        incident_id=uuid4(), source_system=SourceSystem(body.source_system),
        fingerprint=body.fingerprint or uuid4().hex, title=body.title,
        description=body.description, provider_severity=body.provider_severity,
        pagerduty_id=body.pagerduty_id, servicenow_id=body.servicenow_id,
        raw_payload=body.raw_payload, created_at=datetime.now(timezone.utc),
    )

    async with deps.uow_factory(principal.scope) as uow:
        if idempotency_key:
            existing = await uow.investigations.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                response.status_code = 200
                return _summary(existing)
        inv = await uow.investigations.create(
            incident=incident, title=body.title, team_id=team_id, status="running",
            idempotency_key=idempotency_key,
        )
        await uow.audit.record(
            category="api", action="investigation.created", actor_id=principal.subject,
            investigation_id=inv.id, request_id=_rid(),
            result_summary=f"team={team_id} severity={inv.severity}",
            metadata={"source_system": body.source_system.value, "idempotency_key": idempotency_key},
        )
        created = _summary(inv)
        inv_id = inv.id

    # start the workflow only after the create transaction has committed
    status = await orchestrator.start(
        investigation_id=inv_id, incident=incident, triage_hint=None, scope=principal.scope)
    async with deps.uow_factory(principal.scope) as uow:
        await uow.investigations.set_status(inv_id, status.status, severity=status.severity)
    created.status = status.status
    return created


@router.get("", response_model=Page[InvestigationSummary])
async def list_investigations(
    principal: Principal = Depends(require_roles(Role.viewer, Role.responder, Role.approver)),
    deps: AppDeps = Depends(get_app_deps),
    status: Optional[str] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=0, ge=0, le=100),
) -> Page[InvestigationSummary]:
    page_size = limit or deps.settings.default_page_size
    after = decode_cursor(cursor) if cursor else None
    if cursor and after is None:
        raise BadRequest("invalid cursor", code="invalid_cursor")
    async with deps.uow_factory(principal.scope) as uow:
        rows = await uow.investigations.list(limit=page_size + 1, after=after, status=status)
    next_cursor = None
    if len(rows) > page_size:
        rows = rows[:page_size]
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at, last.id)
    return Page[InvestigationSummary](items=[_summary(r) for r in rows], next_cursor=next_cursor)


@router.get("/{investigation_id}", response_model=InvestigationDetail)
async def get_investigation(
    investigation_id: UUID,
    principal: Principal = Depends(require_roles(Role.viewer, Role.responder, Role.approver)),
    deps: AppDeps = Depends(get_app_deps),
) -> InvestigationDetail:
    async with deps.uow_factory(principal.scope) as uow:
        inv = await uow.investigations.get(investigation_id)
    if inv is None:
        raise NotFound("investigation not found", code="not_found")
    state = inv.state or {}
    return InvestigationDetail(
        id=inv.id, status=inv.status, severity=inv.severity, title=inv.title,
        recommended_triage=state.get("recommended_triage"),
        created_at=inv.created_at, updated_at=inv.updated_at,
        incident_id=inv.incident_id, team_id=inv.team_id,
        knowledge_summary=state.get("knowledge_summary"),
        knowledge_degraded=state.get("knowledge_degraded"),
        findings=state.get("knowledge_findings") or [],
        citations=state.get("citations") or [],
        classification=state.get("classification") or {},
        initial_hypothesis=state.get("initial_hypothesis") or {},
    )


@router.get("/{investigation_id}/stream")
async def stream_investigation(
    investigation_id: UUID,
    principal: Principal = Depends(require_roles(Role.viewer, Role.responder, Role.approver)),
    deps: AppDeps = Depends(get_app_deps),
    orchestrator: OrchestratorClient = Depends(require_orchestrator),
) -> StreamingResponse:
    # confirm visibility (scoped) before streaming
    async with deps.uow_factory(principal.scope) as uow:
        inv = await uow.investigations.get(investigation_id)
    if inv is None:
        raise NotFound("investigation not found", code="not_found")

    settings = deps.settings

    async def _events():
        terminal = {"completed", "failed", "escalated", "dropped", "closed_rejected"}
        for _ in range(settings.sse_max_polls):
            status = await orchestrator.get_status(investigation_id, principal.scope)
            yield f"data: {json.dumps(status.model_dump(mode='json'))}\n\n"
            if status.status in terminal or status.status == "awaiting_approval":
                return
            await asyncio.sleep(settings.sse_poll_interval_s)

    return StreamingResponse(_events(), media_type="text/event-stream")


def _rid() -> str:
    return uuid4().hex
